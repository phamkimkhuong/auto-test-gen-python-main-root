def sum_even_numbers(numbers: list[int]) -> int:
    total = 0

    for n in numbers:
        if n < 0:
            continue

        if n > 100:
            break

        if n % 2 == 0:
            total += n

    return total


def count_non_empty_names(names: list[str]) -> int:
    count = 0

    for name in names:
        if name.strip() == "":
            continue

        count += 1

    return count
