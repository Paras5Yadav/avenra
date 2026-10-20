class DomainError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def require(condition, code, message, status=400):
    if not condition:
        raise DomainError(code, message, status)
