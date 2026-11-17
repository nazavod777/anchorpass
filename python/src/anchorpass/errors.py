class AnchorpassError(Exception):
    """Stable, credential-free error with the failing operation stage."""

    def __init__(self, code: str, stage: str, message: str = "") -> None:
        self.code = code
        self.stage = stage
        super().__init__(f"{code} at {stage}" + (f": {message}" if message else ""))
