"""Small statistics helpers."""


def median(values):
    """Return the median of a non-empty sequence of numbers.

    For an odd number of values, the median is the middle value once the
    values are sorted. For an even number of values, the median is the mean
    of the two middle values once the values are sorted.

    Raises ValueError if `values` is empty.
    """
    n = len(values)
    mid = n // 2
    if n % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2
