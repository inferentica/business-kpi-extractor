"""The control plane with its AI shut, for an owner-built company's run: any AI or OCR call fails the run loudly instead
of being made. The owner-locked refresh reads by code alone (owner/refresh.py); a call reaching here is a bug."""
from __future__ import annotations

from ..ops.control import ControlError

_AI_OPERATIONS = {"ai", "ocr"}


class AiRefused(ControlError):
    pass


class NoAi:
    def __init__(self, control):
        self._control = control
        self.refused = 0

    def call(self, operation: str, **payload):
        if operation in _AI_OPERATIONS:
            self.refused += 1
            raise AiRefused(f"{operation} refused: an owner-built company is never read by AI")
        return self._control.call(operation, **payload)

    def ai(self, *args, **kwargs):
        self.refused += 1
        raise AiRefused("ai refused: an owner-built company is never read by AI")

    def ocr(self, *args, **kwargs):
        self.refused += 1
        raise AiRefused("ocr refused: an owner-built company is never read by AI")

    def __getattr__(self, name):
        return getattr(self._control, name)
