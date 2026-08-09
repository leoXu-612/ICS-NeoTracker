from __future__ import annotations

import unittest

import numpy as np

from neo_tracker.presets import color_marker_preset
from neo_tracker.roi import RectangularROI
from neo_tracker.ui.review_response import ReviewResponseService
from neo_tracker.ui.review_response_worker import ReviewResponseWorker


def red_dot_frame(x: float, shape: tuple[int, int, int] = (48, 64, 3)) -> np.ndarray:
    yy, xx = np.indices(shape[:2])
    frame = np.zeros(shape, dtype=np.uint8)
    frame[(xx - x) ** 2 + (yy - 24.0) ** 2 <= 3.0**2, 0] = 255
    return frame


class ReviewResponseWorkerTests(unittest.TestCase):
    def test_worker_computes_read_only_response_without_committing_cache(self) -> None:
        frame = red_dot_frame(20.0)
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 64, 48), tolerance=0.08)
        pipeline.debug_history_limit = 0
        pipeline.run([frame], fps=20.0)
        owner = object()
        service = ReviewResponseService()
        request = service.prepare_request(owner, pipeline, pipeline.results[0], frame)
        completed: list[object] = []
        worker = ReviewResponseWorker(request)
        worker.completed.connect(completed.append)

        worker.run()

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].source, "recomputed")
        self.assertFalse(completed[0].response_map.flags.writeable)
        self.assertEqual(len(service), 0)

    def test_worker_honors_cancel_before_observation(self) -> None:
        frame = red_dot_frame(20.0)
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 64, 48), tolerance=0.08)
        pipeline.run([frame], fps=20.0)
        service = ReviewResponseService()
        request = service.prepare_request(object(), pipeline, pipeline.results[0], frame)
        computed: list[bool] = []
        canceled: list[bool] = []
        worker = ReviewResponseWorker(
            request,
            computer=lambda _request: computed.append(True),  # type: ignore[arg-type,return-value]
        )
        worker.canceled.connect(lambda: canceled.append(True))

        worker.request_cancel()
        worker.run()

        self.assertEqual(computed, [])
        self.assertEqual(canceled, [True])


if __name__ == "__main__":
    unittest.main()
