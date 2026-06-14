from steps_left import from_goal


def test_from_goal_reports_remaining_and_per_day(capsys):
    from_goal(so_far=40_000, target=100_000, days_left=3)

    out = capsys.readouterr().out
    assert "60,000 steps left to reach 100,000 steps" in out
    assert "20,000 steps per day" in out


def test_from_goal_rounds_per_day(capsys):
    from_goal(so_far=0, target=10_000, days_left=3)

    out = capsys.readouterr().out
    # 10,000 / 3 = 3333.33 -> rounded to 3,333
    assert "3,333 steps per day" in out


def test_from_goal_zero_days_left_uses_remaining_as_per_day(capsys):
    # days_left == 0 is falsy, so per_day falls back to the full remaining count
    from_goal(so_far=10_000, target=100_000, days_left=0)

    out = capsys.readouterr().out
    assert "90,000 steps per day" in out


def test_from_goal_already_past_target_goes_negative(capsys):
    from_goal(so_far=120_000, target=100_000, days_left=2)

    out = capsys.readouterr().out
    assert "-20,000 steps left to reach 100,000 steps" in out
