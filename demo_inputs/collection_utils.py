def count_positive_numbers(numbers):
    count = 0

    for number in numbers:
        if number <= 0:
            continue

        count += 1

    return count
def find_first_large_number(numbers, threshold):
    for number in numbers:
        if number > threshold:
            return number

    return None