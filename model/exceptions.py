class ModelError(Exception):
    pass


class ConfigurationError(ModelError):
    pass


class ValidationError(ModelError):
    pass
