# vòng lặp có nhiều nhánh
# vòng lặp duyệt danh sách điểm, có branch score < 0, score >= 80, score >= 50, và nhánh còn lại.
def count_valid_scores(scores):
    passed = 0

    for score in scores:
        if score < 0:
            continue

        if score > 100:
            break

        if score >= 50:
            passed += 1

    return passed
