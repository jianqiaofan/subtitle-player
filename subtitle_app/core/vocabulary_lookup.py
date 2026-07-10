"""为生词表条目补充注音/音标、词性与释义。"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from core.config import APP_DIR

DEFAULT_ECDICT_PATH = APP_DIR / "data" / "ecdict.db"
DEFAULT_JAMDICT_PATH = APP_DIR / "data" / "jamdict.db"

WORDNET_POS_ZH = {
    "n": "名词",
    "v": "动词",
    "a": "形容词",
    "s": "形容词",
    "r": "副词",
}

JANOME_POS_ZH = {
    "名詞": "名词",
    "動詞": "动词",
    "形容詞": "形容词",
    "副詞": "副词",
    "連体詞": "连体词",
    "感動詞": "感叹词",
    "接続詞": "连词",
    "助詞": "助词",
    "助動詞": "助动词",
    "記号": "符号",
    "接頭詞": "前缀",
    "接尾詞": "后缀",
    "フィラー": "填充词",
}

JAMDICT_POS_ZH = {
    "noun": "名词",
    "verb": "动词",
    "adjective": "形容词",
    "adverb": "副词",
    "interjection": "感叹词",
    "conjunction": "连词",
    "particle": "助词",
    "prefix": "前缀",
    "suffix": "后缀",
    "expression": "惯用语",
    "auxiliary verb": "助动词",
    "counter": "量词",
    "pronoun": "代词",
    "preposition": "介词",
    "numeral": "数词",
}


@lru_cache(maxsize=1)
def _ecdict_connection(db_path: str) -> sqlite3.Connection | None:
    path = Path(db_path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_ecdict_path(custom_path: str = "") -> Path | None:
    if custom_path.strip():
        path = Path(custom_path.strip())
        if path.is_file():
            return path
    if DEFAULT_ECDICT_PATH.is_file():
        return DEFAULT_ECDICT_PATH
    return None


def _resolve_jamdict_db_path() -> Path | None:
    if DEFAULT_JAMDICT_PATH.is_file():
        return DEFAULT_JAMDICT_PATH
    try:
        import jamdict_data

        bundled = Path(jamdict_data.__file__).resolve().parent / "jamdict.db"
        if bundled.is_file():
            return bundled
    except ImportError:
        pass
    return None


def _clean_gloss(text: str, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", text.replace("\\n", " ").replace("\n", " ")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _katakana_to_hiragana(text: str) -> str:
    if not text or text == "*":
        return text
    try:
        import jaconv

        return jaconv.kata2hira(text)
    except ImportError:
        chars: list[str] = []
        for ch in text:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30F6:
                chars.append(chr(code - 0x60))
            else:
                chars.append(ch)
        return "".join(chars)


def _lookup_english_with_ecdict(word: str, db_path: Path) -> tuple[str, str, str] | None:
    conn = _ecdict_connection(str(db_path.resolve()))
    if conn is None:
        return None
    key = word.lower()
    row = conn.execute(
        """
        SELECT phonetic, pos, translation, definition
        FROM stardict
        WHERE word = ? COLLATE NOCASE
        LIMIT 1
        """,
        (key,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT phonetic, pos, translation, definition
            FROM stardict
            WHERE sw = ? COLLATE NOCASE
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    if row is None:
        return None

    phonetic = str(row["phonetic"] or "").strip()
    if phonetic and not phonetic.startswith("/"):
        phonetic = f"/{phonetic.strip('/')}/"

    pos = str(row["pos"] or "").strip()
    translation = str(row["translation"] or "").strip()
    definition = str(row["definition"] or "").strip()
    meaning = translation or definition
    meaning = _clean_gloss(meaning.replace("\\n", "；").replace("\n", "；"))
    return phonetic, pos, meaning


def _ensure_wordnet() -> None:
    import nltk

    for resource in ("corpora/wordnet", "corpora/omw-1.4"):
        try:
            nltk.data.find(resource)
        except LookupError:
            package = resource.split("/")[-1]
            nltk.download(package, quiet=True)


def _lookup_english_with_wordnet(word: str) -> tuple[str, str, str]:
    try:
        from nltk.corpus import wordnet as wn
    except ImportError as exc:
        raise RuntimeError(
            "英语词典回退需要 nltk。\n请运行：pip install nltk"
        ) from exc

    _ensure_wordnet()

    synsets = wn.synsets(word.lower())
    if not synsets:
        return "", "", ""

    primary = synsets[0]
    pos = WORDNET_POS_ZH.get(primary.pos(), primary.pos())
    definition = _clean_gloss(primary.definition())
    return "", pos, definition


ARPABET_TO_IPA = {
    "AA": "ɑ",
    "AE": "æ",
    "AH": "ʌ",
    "AO": "ɔ",
    "AW": "aʊ",
    "AY": "aɪ",
    "B": "b",
    "CH": "tʃ",
    "D": "d",
    "DH": "ð",
    "EH": "ɛ",
    "ER": "ɜr",
    "EY": "eɪ",
    "F": "f",
    "G": "ɡ",
    "HH": "h",
    "IH": "ɪ",
    "IY": "i",
    "JH": "dʒ",
    "K": "k",
    "L": "l",
    "M": "m",
    "N": "n",
    "NG": "ŋ",
    "OW": "oʊ",
    "OY": "ɔɪ",
    "P": "p",
    "R": "r",
    "S": "s",
    "SH": "ʃ",
    "T": "t",
    "TH": "θ",
    "UH": "ʊ",
    "UW": "u",
    "V": "v",
    "W": "w",
    "Y": "j",
    "Z": "z",
    "ZH": "ʒ",
}


def _arpabet_to_ipa(phones: str) -> str:
    tokens = phones.split()
    ipa_parts: list[str] = []
    for token in tokens:
        stress = ""
        base = token
        if token[-1:].isdigit():
            stress = token[-1]
            base = token[:-1]
        ipa = ARPABET_TO_IPA.get(base)
        if ipa is None:
            return ""
        if stress == "1":
            ipa_parts.append("ˈ" + ipa)
        elif stress == "2":
            ipa_parts.append("ˌ" + ipa)
        else:
            ipa_parts.append(ipa)
    if not ipa_parts:
        return ""
    return "/" + "".join(ipa_parts) + "/"


def _lookup_english_phonetic(word: str) -> str:
    try:
        import pronouncing
    except ImportError:
        return ""

    phones_list = pronouncing.phones_for_word(word.lower())
    if not phones_list:
        return ""
    return _arpabet_to_ipa(phones_list[0])


def _lookup_japanese_with_janome(word: str) -> tuple[str, str, str]:
    from janome.tokenizer import Tokenizer

    readings: list[str] = []
    pos_labels: list[str] = []
    for token in Tokenizer().tokenize(word):
        surface = token.surface.strip()
        if not surface:
            continue
        reading = token.phonetic
        if not reading or reading == "*":
            reading = surface
        else:
            reading = _katakana_to_hiragana(reading)
        if reading and reading not in readings:
            readings.append(reading)
        pos_main = token.part_of_speech.split(",")[0]
        pos_label = JANOME_POS_ZH.get(pos_main, pos_main)
        if pos_label not in pos_labels:
            pos_labels.append(pos_label)
    return "、".join(readings), "、".join(pos_labels), ""


def _pick_japanese_glosses(sense) -> str:
    zh_parts: list[str] = []
    en_parts: list[str] = []
    for gloss in sense.glosses:
        text = str(getattr(gloss, "text", gloss) or "").strip()
        if not text:
            continue
        lang = str(getattr(gloss, "lang", "") or "").lower()
        if lang in {"zh", "cmn", "zho", "chs", "cht"}:
            zh_parts.append(text)
        else:
            en_parts.append(text)
    parts = zh_parts or en_parts
    return _clean_gloss("；".join(parts))


@lru_cache(maxsize=1)
def _jamdict_instance():
    db_path = _resolve_jamdict_db_path()
    if db_path is None:
        return None
    try:
        from jamdict import Jamdict

        return Jamdict(db_file=str(db_path))
    except Exception:
        return None


def _lookup_japanese_with_jamdict(word: str) -> tuple[str, str, str]:
    jam = _jamdict_instance()
    if jam is None:
        return "", "", ""

    result = jam.lookup(word)
    if not result.entries:
        return "", "", ""

    entry = result.entries[0]
    readings: list[str] = []
    for kana in entry.kana_forms:
        text = _katakana_to_hiragana(str(getattr(kana, "text", "") or "").strip())
        if text and text not in readings:
            readings.append(text)
    reading = "、".join(readings[:3])

    pos_labels: list[str] = []
    meanings: list[str] = []
    for sense in entry.senses:
        for pos in getattr(sense, "pos", []) or []:
            label = JAMDICT_POS_ZH.get(str(pos).lower(), str(pos))
            if label not in pos_labels:
                pos_labels.append(label)
        gloss = _pick_japanese_glosses(sense)
        if gloss and gloss not in meanings:
            meanings.append(gloss)

    pos = "、".join(pos_labels[:4])
    definition = _clean_gloss("；".join(meanings[:3]))
    return reading, pos, definition


def _lookup_japanese(word: str) -> tuple[str, str, str]:
    reading, pos, definition = _lookup_japanese_with_janome(word)
    jamdict_hit = _lookup_japanese_with_jamdict(word)
    if jamdict_hit != ("", "", ""):
        jam_reading, jam_pos, jam_def = jamdict_hit
        if jam_reading:
            reading = jam_reading
        if jam_pos:
            pos = jam_pos
        if jam_def:
            definition = jam_def
    return reading, pos, definition


def lookup_word_lexicon(
    word: str,
    language: str,
    *,
    ecdict_path: str = "",
) -> tuple[str, str, str]:
    """返回 (注音或音标, 词性, 释义)。"""
    if language == "en":
        db_path = _resolve_ecdict_path(ecdict_path)
        if db_path is not None:
            hit = _lookup_english_with_ecdict(word, db_path)
            if hit is not None:
                phonetic, pos, meaning = hit
                if not phonetic:
                    phonetic = _lookup_english_phonetic(word)
                return phonetic, pos, meaning

        phonetic = _lookup_english_phonetic(word)
        pos, meaning = "", ""
        try:
            _, pos, meaning = _lookup_english_with_wordnet(word)
        except RuntimeError:
            raise
        except Exception:
            pass
        return phonetic, pos, meaning

    if language == "ja":
        return _lookup_japanese(word)

    return "", "", ""


def enrich_vocabulary_entries(
    entries,
    language: str,
    *,
    ecdict_path: str = "",
):
    if language not in {"en", "ja"}:
        return entries

    enriched = []
    for entry in entries:
        reading, pos, definition = lookup_word_lexicon(
            entry.word,
            language,
            ecdict_path=ecdict_path,
        )
        enriched.append(
            replace(
                entry,
                reading=reading,
                pos=pos,
                definition=definition,
            )
        )
    return enriched
