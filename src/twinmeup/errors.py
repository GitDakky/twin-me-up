"""Error hierarchy. Every failure the service raises deliberately is a TwinMeUpError."""


class TwinMeUpError(Exception):
    """Base class for deliberate failures."""


class AuthorityError(TwinMeUpError):
    """An actor attempted an operation reserved for a human principal."""


class StateError(TwinMeUpError):
    """The operation is invalid for the record's current state."""


class NotFoundError(TwinMeUpError):
    """The requested record does not exist."""


class IntegrityError(TwinMeUpError):
    """Stored data or the audit chain failed verification."""


class ResponderError(TwinMeUpError):
    """A model responder failed or returned an unusable answer."""
