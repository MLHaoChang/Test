"""Domain-specific errors the core helpers raise on purpose.

Every error below signals a problem with the input the app was given (an
unparseable number, an invalid ISIN, a date or time that cannot exist, a
bad PG_TODAY value), never a bug in this code. A caller that wants to
turn a bad document into a review item (plan 5.3.5) instead of failing
the whole import catches the specific subclass it cares about; anything
uncaught still carries a plain-English message.
"""


class PlaygroundError(Exception):
    """Base class for every domain-specific error this app raises on purpose."""


class NumberFormatError(PlaygroundError):
    """A number string does not match the locale format its parser function expects.

    Raised by `core.numbers`. The function never guesses a different
    locale for a string it cannot parse; the document's own layout
    decides which parsing function to call.
    """


class DateFormatError(PlaygroundError):
    """A date string does not match any format `core.dates` recognises, or is not a real calendar date."""


class NonExistentLocalTimeError(PlaygroundError):
    """A local Europe/Berlin time falls in the spring daylight-saving-time gap.

    Every year, on the last Sunday in March, clocks jump from 02:00
    straight to 03:00 CEST; nothing between them ever happened, so no
    document ever really booked a transaction then.
    """


class InvalidIsinError(PlaygroundError):
    """A value is not a syntactically valid ISIN, or fails the check-digit test."""


class InvalidClockSettingError(PlaygroundError):
    """PG_TODAY is set to something other than a valid YYYY-MM-DD date."""
