import warnings

# CLI/test code opens short-lived SQLite handles that the process closes on exit.
warnings.filterwarnings("ignore", category=ResourceWarning)
