# class EntityAmbiguousError(Exception):
#     """Raised when Vector Search finds multiple conflicting matches."""

#     def __init__(self,message:str,options:list):
#         super().__init__(message)
#         self.message=message
#         self.options=options


class EntityAmbiguousError(Exception):
    def __init__(self, message: str, options: list):
        super().__init__(message)
        self.options = options
        self.message = message

class MultiEntityAmbiguousError(Exception):
    def __init__(self, message: str, ambiguities: dict):
        super().__init__(message)
        # ambiguities format: {"raw_value": ["Col:Val1", "Col:Val2"]}
        self.ambiguities = ambiguities