# services/face_detection_benchmark.py
# Version 01.00.00.01 (Phase 7.0 – People / Face Detection)
# Performance benchmarking for face detection
# Targets: Apple Photos, Google Photos, Microsoft Photos performance levels
# ------------------------------------------------------

"""
Face Detection Benchmarking

This module provides benchmarking utilities for face detection performance.
It helps track and compare our performance against industry standards:

Target Performance (Industry Standards):
- Apple Photos: ~100-200 faces/second (M1/M2 chips with Neural Engine)
- Google Photos: ~50-100 faces/second (server-side processing)
- Microsoft Photos: ~30-50 faces/second (local CPU processing)

Our Target: 20-100 faces/second (depending on hardware)
- GPU (CUDA): 50-100 faces/second
- CPU (modern): 20-50 faces/second
"""

import time
import json
import os
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a face detection benchmark run."""

    # Timing
    start_time: str
    duration_seconds: float

    # Input
    photos_count: int
    total_file_size_mb: float

    # Output
    faces_detected: int
    photos_with_faces: int
    errors: int

    # Performance metrics
    faces_per_second: float
    photos_per_second: float
    mb_per_second: float

    # Hardware info
    hardware_type: str  # 'GPU' or 'CPU'
    provider: str       # e.g., 'CUDAExecutionProvider'

    # Quality metrics
    avg_faces_per_photo: float
    detection_rate: float  # percentage of photos with faces

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def get_performance_rating(self) -> str:
        """
        Get performance rating compared to industry standards.

        Returns:
            str: 'Excellent', 'Good', 'Fair', or 'Poor'
        """
        fps = self.faces_per_second

        if self.hardware_type == 'GPU':
            if fps >= 50:
                return 'Excellent (comparable to Apple Photos)'
            elif fps >= 30:
                return 'Good (comparable to Google Photos)'
            elif fps >= 20:
                return 'Fair (comparable to Microsoft Photos)'
            else:
                return 'Poor (below industry standards)'
        else:  # CPU
            if fps >= 30:
                return 'Excellent (above typical CPU performance)'
            elif fps >= 20:
                return 'Good (meets industry CPU standards)'
            elif fps >= 10:
                return 'Fair (acceptable for CPU processing)'
            else:
                return 'Poor (below acceptable CPU performance)'

    def print_summary(self):
        """Print a formatted summary of the benchmark."""
        print("\n" + "="*70)
        print("FACE DETECTION BENCHMARK RESULTS")
        print("="*70)
        print(f"Date/Time: {self.start_time}")
        print(f"Hardware: {self.hardware_type} ({self.provider})")
        print()
        print("INPUT:")
        print(f"  Photos processed: {self.photos_count}")
        print(f"  Total size: {self.total_file_size_mb:.2f} MB")
        print()
        print("OUTPUT:")
        print(f"  Faces detected: {self.faces_detected}")
        print(f"  Photos with faces: {self.photos_with_faces}")
        print(f"  Errors: {self.errors}")
        print(f"  Detection rate: {self.detection_rate:.1f}%")
        print(f"  Avg faces/photo: {self.avg_faces_per_photo:.2f}")
        print()
        print("PERFORMANCE:")
        print(f"  Duration: {self.duration_seconds:.2f}s")
        print(f"  Faces/second: {self.faces_per_second:.2f}")
        print(f"  Photos/second: {self.photos_per_second:.2f}")
        print(f"  MB/second: {self.mb_per_second:.2f}")
        print()
        print(f"RATING: {self.get_performance_rating()}")
        print("="*70)

    def save_to_file(self, filepath: str):
        """Save benchmark result to JSON file."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Benchmark saved to {filepath}")


class FaceDetectionBenchmark:
    """
    Benchmarking tool for face detection performance.

    Usage:
        benchmark = FaceDetectionBenchmark()
        benchmark.start()

        # ... run face detection ...

        result = benchmark.finish(stats)
        result.print_summary()
    """

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.photo_paths = []

    def start(self):
        """Start the benchmark timer."""
        self.start_time = time.time()
        logger.info("Benchmark started")

    def finish(self,
              stats: Dict,
              photo_paths: List[str],
              hardware_type: str = 'Unknown',
              provider: str = 'Unknown') -> BenchmarkResult:
        """
        Finish benchmark and generate results.

        Args:
            stats: Statistics dict from face detection
            photo_paths: List of photo paths processed
            hardware_type: 'GPU' or 'CPU'
            provider: Provider name (e.g., 'CUDAExecutionProvider')

        Returns:
            BenchmarkResult with all metrics
        """
        self.end_time = time.time()
        duration = self.end_time - self.start_time

        # Calculate file sizes
        total_size_bytes = 0
        for path in photo_paths:
            try:
                total_size_bytes += os.path.getsize(path)
            except:
                pass
        total_size_mb = total_size_bytes / (1024 * 1024)

        # Extract stats
        photos_count = stats.get('photos_processed', 0)
        faces_detected = stats.get('faces_detected', 0)
        photos_with_faces = stats.get('photos_with_faces', 0)
        errors = stats.get('errors', 0)

        # Calculate performance metrics
        faces_per_second = faces_detected / duration if duration > 0 else 0
        photos_per_second = photos_count / duration if duration > 0 else 0
        mb_per_second = total_size_mb / duration if duration > 0 else 0

        # Calculate quality metrics
        avg_faces_per_photo = faces_detected / photos_count if photos_count > 0 else 0
        detection_rate = (photos_with_faces / photos_count * 100) if photos_count > 0 else 0

        result = BenchmarkResult(
            start_time=datetime.fromtimestamp(self.start_time).isoformat(),
            duration_seconds=duration,
            photos_count=photos_count,
            total_file_size_mb=total_size_mb,
            faces_detected=faces_detected,
            photos_with_faces=photos_with_faces,
            errors=errors,
            faces_per_second=faces_per_second,
            photos_per_second=photos_per_second,
            mb_per_second=mb_per_second,
            hardware_type=hardware_type,
            provider=provider,
            avg_faces_per_photo=avg_faces_per_photo,
            detection_rate=detection_rate
        )

        logger.info(f"Benchmark finished: {faces_per_second:.2f} faces/second")
        return result


def compare_with_industry_standards(result: BenchmarkResult) -> Dict:
    """
    Compare benchmark result with industry standards.

    Args:
        result: BenchmarkResult to compare

    Returns:
        Dict with comparison metrics
    """
    # Industry standard estimates (faces per second)
    standards = {
        'Apple Photos (M2 + Neural Engine)': 150,
        'Google Photos (Server)': 75,
        'Microsoft Photos (CPU)': 40,
        'Our Target (GPU)': 50,
        'Our Target (CPU)': 25,
    }

    our_performance = result.faces_per_second

    comparison = {
        'our_performance': our_performance,
        'standards': standards,
        'percentages': {}
    }

    for name, standard_fps in standards.items():
        percentage = (our_performance / standard_fps * 100) if standard_fps > 0 else 0
        comparison['percentages'][name] = percentage

    return comparison


def print_industry_comparison(result: BenchmarkResult):
    """Print comparison with industry standards."""
    comparison = compare_with_industry_standards(result)

    print("\n" + "="*70)
    print("COMPARISON WITH INDUSTRY STANDARDS")
    print("="*70)
    print(f"Our Performance: {comparison['our_performance']:.2f} faces/second\n")

    for name, standard in comparison['standards'].items():
        percentage = comparison['percentages'][name]
        bar_length = int(percentage / 5)  # Scale to fit in terminal
        bar = "█" * min(bar_length, 40)

        status = "✓" if percentage >= 80 else "○"
        print(f"{status} {name:40s} {standard:3.0f} fps  {percentage:5.1f}% {bar}")

    print("="*70 + "\n")
