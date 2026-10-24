class InvoicerError(Exception):
    """Base for every error the service raises on purpose."""


class NotFound(InvoicerError):
    pass


class InvalidState(InvoicerError):
    """The invoice is not in a state that allows the operation."""


class InvalidAmount(InvoicerError):
    pass


class FeatureDisabled(InvoicerError):
    pass
