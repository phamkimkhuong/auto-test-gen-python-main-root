# Bug trong vòng lặp, continue, break, accumulator
def count_valid_scores_buggy(scores):
    passed = 0

    for score in scores:
        if score < 0:
            continue

        if score > 100:
            continue  # BUG: đúng ra phải break hoặc raise, không phải continue

        if score >= 50:
            passed += 1

    return passed