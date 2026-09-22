class SQLValidationError(Exception):
    """Raised when the database rejects a generated SQL query."""
    pass

class EntityAmbiguousError(Exception):
    """Raised when Vector Search finds multiple conflicting matches."""
    
    def __init__(self, message: str, options: list):
        super().__init__(message)
        self.message = message
        self.options = options