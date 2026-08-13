from __future__ import annotations

import math
import unittest

import numpy as np

from neo_tracker.coordinates import LinearWorldCoordinate, PathCoordinate, PolarCoordinate
from neo_tracker.roi import AnnularROI, CircularROI, CurveBandROI, PolygonROI, RectangularROI


class ROICoordinateTests(unittest.TestCase):
    def test_rectangular_roi_mask_and_contains(self) -> None:
        roi = RectangularROI(2, 3, 5, 4)
        mask = roi.mask((12, 12, 3))
        self.assertTrue(mask[4, 4])
        self.assertFalse(mask[1, 1])
        self.assertTrue(roi.contains_point((7, 7)))
        self.assertFalse(roi.contains_point((8, 7)))

    def test_polygon_roi_contains(self) -> None:
        roi = PolygonROI(((0, 0), (10, 0), (10, 10), (0, 10)))
        self.assertTrue(roi.contains_point((5, 5)))
        self.assertFalse(roi.contains_point((12, 5)))

    def test_annular_roi(self) -> None:
        roi = AnnularROI(center=(20, 20), inner_radius=5, outer_radius=10)
        self.assertTrue(roi.contains_point((28, 20)))
        self.assertFalse(roi.contains_point((22, 20)))
        self.assertFalse(roi.contains_point((35, 20)))

    def test_roi_masks_are_cached_by_shape_and_read_only(self) -> None:
        rois = [
            RectangularROI(2.0, 3.0, 8.0, 6.0),
            PolygonROI(((2.0, 2.0), (12.0, 3.0), (7.0, 14.0))),
            CircularROI((8.0, 8.0), 5.0),
            AnnularROI((8.0, 8.0), 2.0, 6.0),
            CurveBandROI(((1.0, 1.0), (8.0, 9.0), (15.0, 4.0)), 2.0),
        ]
        for roi in rois:
            first = roi.mask((20, 24, 3))
            second = roi.mask((20, 24, 3))
            self.assertIs(first, second)
            self.assertFalse(first.flags.writeable)
            self.assertIsNot(first, roi.mask((21, 24, 3)))

    def test_roi_masks_match_dense_coordinate_reference(self) -> None:
        shape = (43, 57, 3)
        rois = [
            RectangularROI(4.25, 6.5, 31.75, 22.25),
            RectangularROI(-8.5, 4.25, 20.0, 17.5),
            PolygonROI(((3.5, 5.0), (42.0, 2.5), (50.5, 31.0), (17.0, 39.5))),
            PolygonROI(((-12.0, -6.0), (28.0, 1.5), (63.0, 30.0), (6.0, 48.0))),
            CircularROI((29.25, 20.75), 14.5),
            CircularROI((-2.5, 21.0), 8.25),
            CircularROI((90.0, 90.0), 5.0),
            AnnularROI((29.25, 20.75), 5.25, 16.5),
            AnnularROI((55.0, 3.0), 2.25, 9.5),
            CurveBandROI(((2.5, 35.0), (20.0, 7.5), (36.0, 29.0), (54.0, 10.0)), 3.75),
            CurveBandROI(((-18.0, 8.0), (11.0, 8.0), (11.0, 8.0), (71.0, 48.0)), 4.25),
        ]

        def dense_reference(roi) -> np.ndarray:
            h, w = shape[:2]
            yy, xx = np.indices((h, w))
            if isinstance(roi, RectangularROI):
                return (
                    (xx >= roi.x)
                    & (xx <= roi.x + roi.width)
                    & (yy >= roi.y)
                    & (yy <= roi.y + roi.height)
                )
            if isinstance(roi, CircularROI):
                cx, cy = roi.center
                return (xx - cx) ** 2 + (yy - cy) ** 2 <= roi.radius**2
            if isinstance(roi, AnnularROI):
                cx, cy = roi.center
                rr2 = (xx - cx) ** 2 + (yy - cy) ** 2
                return (rr2 >= roi.inner_radius**2) & (rr2 <= roi.outer_radius**2)
            if isinstance(roi, PolygonROI):
                inside = np.zeros((h, w), dtype=bool)
                points = np.asarray(roi.points, dtype=float)
                x_vertices = points[:, 0]
                y_vertices = points[:, 1]
                j = len(points) - 1
                for i in range(len(points)):
                    yi = y_vertices[i]
                    yj = y_vertices[j]
                    xi = x_vertices[i]
                    xj = x_vertices[j]
                    inside ^= ((yi > yy) != (yj > yy)) & (
                        xx < (xj - xi) * (yy - yi) / (yj - yi + 1e-12) + xi
                    )
                    j = i
                return inside
            distance = np.full((h, w), np.inf, dtype=float)
            points = np.asarray(roi.polyline, dtype=float)
            for start, end in zip(points[:-1], points[1:]):
                sx, sy = start
                ex, ey = end
                vx = ex - sx
                vy = ey - sy
                denom = vx * vx + vy * vy + 1e-12
                t = np.clip(((xx - sx) * vx + (yy - sy) * vy) / denom, 0.0, 1.0)
                projection_x = sx + t * vx
                projection_y = sy + t * vy
                distance = np.minimum(
                    distance,
                    np.hypot(xx - projection_x, yy - projection_y),
                )
            return distance <= roi.half_width

        for roi in rois:
            with self.subTest(roi=roi.name):
                expected = dense_reference(roi)
                actual = roi.mask(shape)
                self.assertTrue(np.array_equal(actual, expected))
                self.assertFalse(actual.flags.writeable)

    def test_linear_world_mapping_roundtrip(self) -> None:
        mapping = LinearWorldCoordinate.from_calibration_rod((10, 10), (110, 10), 0.5, "m")
        world = mapping.image_to_state_space((60, 0))
        self.assertAlmostEqual(world["x_world"], 0.25, places=6)
        self.assertAlmostEqual(world["y_world"], 0.05, places=6)
        pixel = mapping.state_to_image_space(world)
        self.assertAlmostEqual(pixel[0], 60.0, places=6)
        self.assertAlmostEqual(pixel[1], 0.0, places=6)

        down = LinearWorldCoordinate.from_calibration_rod(
            (10, 10), (110, 10), 0.5, "m", y_positive="down"
        )
        down_world = down.image_to_state_space((60, 20))
        self.assertAlmostEqual(down_world["x_world"], 0.25, places=6)
        self.assertAlmostEqual(down_world["y_world"], 0.05, places=6)
        self.assertEqual(down.state_to_image_space(down_world), (60.0, 20.0))

        with self.assertRaisesRegex(ValueError, "y_positive"):
            LinearWorldCoordinate.from_calibration_rod(
                (10, 10), (110, 10), 0.5, "m", y_positive="sideways"
            )

    def test_polar_mapping(self) -> None:
        mapping = PolarCoordinate(center_px=(50, 50))
        state = mapping.image_to_state_space((50, 30))
        self.assertAlmostEqual(state["theta"], math.pi / 2.0, places=6)
        pixel = mapping.state_to_image_space({"theta": math.pi / 2.0, "r": 20.0})
        self.assertAlmostEqual(pixel[0], 50.0, places=6)
        self.assertAlmostEqual(pixel[1], 30.0, places=6)

    def test_path_coordinate_roundtrip_near_polyline(self) -> None:
        mapping = PathCoordinate(polyline=((0, 0), (100, 0), (100, 100)))
        state = mapping.image_to_state_space((100, 30))
        self.assertAlmostEqual(state["s"], 130.0, places=6)
        pixel = mapping.state_to_image_space(state)
        self.assertTrue(np.linalg.norm(np.asarray(pixel) - np.asarray((100, 30))) < 1e-6)
        roi = CurveBandROI(polyline=((0, 0), (100, 0), (100, 100)), half_width=5)
        self.assertTrue(roi.contains_point((100, 30)))
        self.assertFalse(roi.contains_point((90, 30)))


if __name__ == "__main__":
    unittest.main()
