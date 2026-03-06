"""TASK-023/024/025: REP_SCORE service unit tests."""
import pytest

from app.services.rep_service import max_reward_for_rep


def test_rep_ceiling_zero():
    assert max_reward_for_rep(0) == 50


def test_rep_ceiling_just_below_100():
    assert max_reward_for_rep(99) == 50


def test_rep_ceiling_at_100():
    assert max_reward_for_rep(100) == 500


def test_rep_ceiling_mid_range():
    assert max_reward_for_rep(300) == 500


def test_rep_ceiling_at_500():
    assert max_reward_for_rep(500) == 5000


def test_rep_ceiling_high():
    assert max_reward_for_rep(2000) == 50_000


def test_rep_ceiling_very_high():
    assert max_reward_for_rep(9999) == 50_000
