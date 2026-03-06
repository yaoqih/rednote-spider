"""Domain exceptions."""


class TaskNotFoundError(ValueError):
    pass


class InvalidTaskTransitionError(ValueError):
    pass
