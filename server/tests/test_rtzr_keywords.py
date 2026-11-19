"""#22: B의 RTZR 키워드 부스팅 목록이 C의 화면 대상 이름을 모두 담고 RTZR 제약을 지키는지 확인한다."""

import re

from hyojason_voice.worker import KEYWORDS
from server.agent.profiles import TARGETS


def test_22_keywords_follow_rtzr_constraints():
    # sommers_ko 키워드: 한글·공백만, 20자 이하, 최대 100개 (B 측정, #22)
    assert len(KEYWORDS) <= 100
    assert len(set(KEYWORDS)) == len(KEYWORDS)
    for word in KEYWORDS:
        assert re.fullmatch(r"[가-힣 ]{1,20}", word), word
        assert word == word.strip() and "  " not in word, word


def test_22_keywords_cover_every_korean_target_name():
    names = {word for profile in TARGETS.values() for word in (profile.label, *profile.aliases)
             if re.fullmatch(r"[가-힣 ]{1,20}", word)}
    assert names - set(KEYWORDS) == set()
