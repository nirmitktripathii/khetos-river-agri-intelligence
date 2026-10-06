import time

# When this package was imported. app.py compares it with the modification times of src/ and data/ to spot a deploy
# that changed them under a running server.
LOADED_NS = time.time_ns()
