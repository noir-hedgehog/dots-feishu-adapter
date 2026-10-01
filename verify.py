"""Offline release gate: dependency skips are failures, not green validation."""
import unittest
suite=unittest.defaultTestLoader.discover('.')
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
