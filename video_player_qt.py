# video_player_qt.py
# Version 1.0.0 dated 2025-11-09
# Video player panel with QMediaPlayer for video playback

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider,
    QLabel, QStyle, QSizePolicy
)
from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from pathlib import Path


class VideoPlayerPanel(QWidget):
    """
    Video player panel with playback controls.

    Features:
    - Video playback with QMediaPlayer
    - Play/pause, seek, volume controls
    - Display video metadata (duration, resolution)
    - Keyboard shortcuts (Space = play/pause, Left/Right = seek)

    Signals:
    - closed: Emitted when player is closed
    """

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_video_path = None
        self.is_seeking = False

        self._setup_ui()
        self._setup_player()
        self._setup_connections()

    def _setup_ui(self):
        """Create UI layout with video widget and controls."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # === Video Widget ===
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(300)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.video_widget, 1)

        # === Control Bar ===
        control_bar = QWidget()
        control_bar.setMaximumHeight(80)
        control_layout = QVBoxLayout(control_bar)
        control_layout.setContentsMargins(10, 5, 10, 5)
        control_layout.setSpacing(5)

        # --- Timeline Slider ---
        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.setEnabled(False)
        control_layout.addWidget(self.timeline_slider)

        # --- Time Labels ---
        time_layout = QHBoxLayout()
        self.time_label = QLabel("0:00")
        self.time_label.setMinimumWidth(50)
        time_layout.addWidget(self.time_label)
        time_layout.addStretch()
        self.duration_label = QLabel("0:00")
        self.duration_label.setMinimumWidth(50)
        self.duration_label.setAlignment(Qt.AlignRight)
        time_layout.addWidget(self.duration_label)
        control_layout.addLayout(time_layout)

        # --- Playback Controls ---
        controls_layout = QHBoxLayout()

        # Play/Pause button
        self.play_button = QPushButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setFixedSize(40, 40)
        self.play_button.setToolTip("Play/Pause (Space)")
        controls_layout.addWidget(self.play_button)

        # Stop button
        self.stop_button = QPushButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setFixedSize(40, 40)
        self.stop_button.setToolTip("Stop")
        controls_layout.addWidget(self.stop_button)

        controls_layout.addSpacing(20)

        # Volume controls
        volume_label = QLabel("🔊")
        controls_layout.addWidget(volume_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setMaximumWidth(100)
        self.volume_slider.setToolTip("Volume")
        controls_layout.addWidget(self.volume_slider)

        controls_layout.addStretch()

        # Metadata label
        self.metadata_label = QLabel("No video loaded")
        self.metadata_label.setStyleSheet("color: #888; font-size: 11px;")
        controls_layout.addWidget(self.metadata_label)

        controls_layout.addSpacing(10)

        # Close button
        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setToolTip("Close Player")
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border-radius: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
        """)
        controls_layout.addWidget(self.close_button)

        control_layout.addLayout(controls_layout)
        layout.addWidget(control_bar)

    def _setup_player(self):
        """Initialize QMediaPlayer and audio output."""
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.7)

        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        # Update timer for smooth timeline updates
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(100)  # Update every 100ms

    def _setup_connections(self):
        """Connect signals and slots."""
        # Player signals
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_error)

        # Control signals
        self.play_button.clicked.connect(self._toggle_play_pause)
        self.stop_button.clicked.connect(self._stop)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.valueChanged.connect(self._on_slider_value_changed)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.close_button.clicked.connect(self._close_player)

        # Update timer
        self.update_timer.timeout.connect(self._update_position)

    def load_video(self, video_path: str, metadata: dict = None):
        """
        Load and prepare video for playback.

        Args:
            video_path: Path to video file
            metadata: Optional video metadata dict (duration, resolution, etc.)
        """
        if not video_path or not Path(video_path).exists():
            print(f"[VideoPlayer] Video file not found: {video_path}")
            return

        self.current_video_path = video_path

        # Load video
        video_url = QUrl.fromLocalFile(str(video_path))
        self.player.setSource(video_url)

        # Update metadata label
        if metadata:
            duration = metadata.get('duration_seconds', 0)
            width = metadata.get('width', 0)
            height = metadata.get('height', 0)
            codec = metadata.get('codec', 'unknown')

            meta_text = f"📹 {width}x{height} | {codec}"
            if duration:
                mins = int(duration // 60)
                secs = int(duration % 60)
                meta_text += f" | {mins}:{secs:02d}"
            self.metadata_label.setText(meta_text)
        else:
            filename = Path(video_path).name
            self.metadata_label.setText(f"📹 {filename}")

        # Enable controls
        self.timeline_slider.setEnabled(True)

        # Start playback automatically
        self.player.play()
        self.update_timer.start()

        print(f"[VideoPlayer] Loaded: {video_path}")

    def _toggle_play_pause(self):
        """Toggle between play and pause."""
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _stop(self):
        """Stop playback and reset position."""
        self.player.stop()
        self.update_timer.stop()

    def _on_duration_changed(self, duration):
        """Update slider range when video duration is known."""
        self.timeline_slider.setRange(0, duration)
        self.duration_label.setText(self._format_time(duration))

    def _on_position_changed(self, position):
        """Update timeline slider when playback position changes."""
        if not self.is_seeking:
            self.timeline_slider.setValue(position)
            self.time_label.setText(self._format_time(position))

    def _update_position(self):
        """Manual position update from timer (smoother than signal alone)."""
        if not self.is_seeking and self.player.playbackState() == QMediaPlayer.PlayingState:
            position = self.player.position()
            self.timeline_slider.setValue(position)
            self.time_label.setText(self._format_time(position))

    def _on_playback_state_changed(self, state):
        """Update play button icon based on playback state."""
        if state == QMediaPlayer.PlayingState:
            self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
            self.update_timer.start()
        else:
            self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            if state == QMediaPlayer.StoppedState:
                self.update_timer.stop()

    def _on_slider_pressed(self):
        """User started dragging timeline slider."""
        self.is_seeking = True

    def _on_slider_released(self):
        """User finished dragging timeline slider - seek to position."""
        self.is_seeking = False
        position = self.timeline_slider.value()
        self.player.setPosition(position)

    def _on_slider_value_changed(self, value):
        """Update time label while dragging slider."""
        if self.is_seeking:
            self.time_label.setText(self._format_time(value))

    def _on_volume_changed(self, value):
        """Update audio volume."""
        volume = value / 100.0
        self.audio_output.setVolume(volume)

    def _on_error(self, error, error_string):
        """Handle playback errors."""
        print(f"[VideoPlayer] Error: {error_string}")
        self.metadata_label.setText(f"❌ Error: {error_string}")

    def _close_player(self):
        """Close the video player."""
        self.player.stop()
        self.update_timer.stop()
        self.closed.emit()

    def _format_time(self, milliseconds):
        """Format milliseconds to MM:SS or H:MM:SS."""
        if milliseconds < 0:
            return "0:00"

        total_seconds = milliseconds // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key_Space:
            self._toggle_play_pause()
            event.accept()
        elif event.key() == Qt.Key_Left:
            # Seek backward 5 seconds
            position = max(0, self.player.position() - 5000)
            self.player.setPosition(position)
            event.accept()
        elif event.key() == Qt.Key_Right:
            # Seek forward 5 seconds
            duration = self.player.duration()
            position = min(duration, self.player.position() + 5000)
            self.player.setPosition(position)
            event.accept()
        elif event.key() == Qt.Key_Escape:
            self._close_player()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """Clean up when widget is closed."""
        self.player.stop()
        self.update_timer.stop()
        super().closeEvent(event)
