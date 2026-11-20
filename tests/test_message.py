"""Decoding of headers, bodies and attachments into the message schema."""

from datetime import UTC, datetime

from conftest import make_raw

from yandex_mail.htmltext import html_to_text
from yandex_mail.message import (
    addresses,
    attachments,
    body_text,
    decode_bytes,
    decode_words,
    message_dict,
    parse,
    read_headers,
)

CRLF = b"\r\n"


def raw(*lines: bytes) -> bytes:
    return CRLF.join(lines)


def test_rfc2047_utf8_koi8_and_cp1251_words():
    message = parse(
        raw(
            b"Subject: =?utf-8?B?0KHRh9GR0YIg0LfQsCDQvdC+0Y/QsdGA0Yw=?=",
            b"From: =?koi8-r?Q?=EF=C1=CF_=ED=CF=D3=DC=CE=C5=D2=C7=CF=D3=C2=D9=D4?= <info@mosenergosbyt.ru>",
            b"To: =?windows-1251?B?yOLg7Q==?= <ivan@yandex.ru>",
            b"",
            b"body",
        )
    )
    headers = read_headers(message)

    assert headers.subject == "Счёт за ноябрь"
    assert headers.sender == {
        "name": "Оао Мосэнергосбыт",
        "address": "info@mosenergosbyt.ru",
    }
    assert headers.to == [{"name": "Иван", "address": "ivan@yandex.ru"}]


def test_multibyte_character_split_between_encoded_words():
    # "Привет" in UTF-8 cut in the middle of a character, as some mailers do.
    encoded = "=?UTF-8?B?0J/RgNC4?= =?UTF-8?B?0LLQtdGC?="
    assert decode_words(encoded) == "Привет"


def test_whitespace_kept_between_word_and_plain_text():
    assert decode_words("Re: =?utf-8?B?0YHRh9GR0YI=?= 123") == "Re: счёт 123"


def test_raw_8bit_headers_in_undeclared_cp1251_and_koi8r():
    message = parse(raw(b"Subject: \xd1\xf7\xe5\xf2 \xe7\xe0 \xec\xe0\xe9", b"", b"x"))
    assert read_headers(message).subject == "Счет за май"
    message = parse(raw(b"Subject: \xf3\xde\xc5\xd4 \xda\xc1 \xcd\xc1\xca", b"", b"x"))
    assert read_headers(message).subject == "Счет за май"


def test_decode_bytes_falls_back_when_label_is_wrong():
    assert decode_bytes("тест".encode("cp1251"), "utf-8") == "тест"
    assert decode_bytes("тест".encode("koi8-r"), "x-unknown") == "тест"
    assert decode_bytes("тест".encode("cp1251"), "win-1251") == "тест"


def test_bodies_in_koi8r_and_windows_1251():
    koi8 = raw(
        b"Content-Type: text/plain; charset=koi8-r",
        b"Content-Transfer-Encoding: 8bit",
        b"",
        "Показания приняты".encode("koi8-r"),
    )
    cp1251 = raw(
        b"Content-Type: text/plain; charset=windows-1251",
        b"Content-Transfer-Encoding: quoted-printable",
        b"",
        b"=CF=EE=EA=E0=E7=E0=ED=E8=FF",
    )
    assert body_text(parse(koi8)) == "Показания приняты"
    assert body_text(parse(cp1251)) == "Показания"


def test_html_only_body_becomes_text():
    message = make_raw(
        body=None,
        html="<html><head><style>p{}</style><title>T</title></head><body>"
        "<p>Сумма&nbsp;к оплате:</p><table><tr><td>Итого</td><td>1&nbsp;234,56</td></tr>"
        "</table><script>alert(1)</script></body></html>",
    )
    assert body_text(parse(message)) == "Сумма к оплате:\n\nИтого 1 234,56"


def test_html_document_in_plain_part_becomes_text():
    # Mosenergosbyt and MosOblEIRC put the HTML source into text/plain too.
    for start in (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">\r\n<html>',
        "\r\n<html>",
    ):
        page = (
            start
            + "<head><title>T</title></head><body><p>Счёт&nbsp;за октябрь</p></body></html>"
        )
        message = make_raw(body=page, html=page)
        assert body_text(parse(message)) == "Счёт за октябрь"


def test_preview_padding_characters_are_dropped():
    # Marketing mail pads the preview text with invisible characters.
    html = "<p>Мои&#774; счёт&zwnj;&nbsp;&#847;&#10240;&#10240;&shy;&#8203; оплачен</p>"
    decomposed_short_i = "и" + chr(0x0306)  # a real letter, kept
    assert html_to_text(html) == f"Мо{decomposed_short_i} счёт оплачен"
    plain = "Счёт" + chr(0x2800) * 3 + chr(0x200C) + " готов"
    assert body_text(parse(make_raw(body=plain))) == "Счёт готов"


def test_plain_text_mentioning_html_stays_as_is():
    message = make_raw(body="Пришлите <html> страницу", html="<p>другое</p>")
    assert body_text(parse(message)) == "Пришлите <html> страницу"


def test_blank_plain_part_falls_back_to_html():
    message = make_raw(body="   \n", html="<p>Настоящий текст</p>")
    assert body_text(parse(message)) == "Настоящий текст"


def test_plain_part_preferred_over_html():
    message = make_raw(body="plain version", html="<p>html version</p>")
    assert body_text(parse(message)) == "plain version"


def test_html_to_text_keeps_preformatted_lines():
    assert html_to_text("<pre>a  b\nc</pre>") == "a b\nc"


def test_attachment_names_in_every_encoding():
    message = parse(
        raw(
            b"Content-Type: multipart/mixed; boundary=XX",
            b"",
            b"--XX",
            b"Content-Type: text/plain; charset=utf-8",
            b"",
            b"see attached",
            b"--XX",
            b"Content-Type: application/pdf",
            b"Content-Disposition: attachment; filename*=utf-8''%D0%A1%D1%87%D1%91%D1%82.pdf",
            b"",
            b"abc",
            b"--XX",
            b"Content-Type: application/pdf",
            b'Content-Disposition: attachment; filename="=?utf-8?B?0JDQutGCLnBkZg==?="',
            b"",
            b"abcd",
            b"--XX",
            b'Content-Type: application/pdf; name="\xd1\xf7\xe5\xf2.pdf"',
            b"Content-Disposition: attachment",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"JVBERi0x",
            b"--XX",
            b"Content-Type: image/png",
            b"Content-ID: <logo>",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"iVBORw==",
            b"--XX--",
        )
    )
    found = [a.info() for a in attachments(message)]

    assert [a["filename"] for a in found] == ["Счёт.pdf", "Акт.pdf", "Счет.pdf", None]
    assert [a["size_bytes"] for a in found] == [3, 4, 6, 4]
    assert [a["inline"] for a in found] == [False, False, False, True]
    assert [a["index"] for a in found] == [1, 2, 3, 4]
    assert body_text(message) == "see attached"


def test_attached_email_is_one_attachment():
    inner = make_raw(
        subject="Inner",
        body="inner body",
        attachments=[("x.pdf", "application/pdf", b"%PDF")],
    )
    message = parse(
        raw(
            b"Content-Type: multipart/mixed; boundary=XX",
            b"",
            b"--XX",
            b"Content-Type: text/plain",
            b"",
            b"outer body",
            b"--XX",
            b"Content-Type: message/rfc822",
            b"",
            inner,
            b"--XX--",
        )
    )
    found = attachments(message)

    assert [a.content_type for a in found] == ["message/rfc822"]
    assert b"Subject: Inner" in found[0].payload()
    assert body_text(message) == "outer body"


def test_address_with_comma_inside_encoded_name():
    # "Иванов, Иван" encoded: the comma must not split the address.
    value = (
        "=?utf-8?B?0JjQstCw0L3QvtCyLCDQmNCy0LDQvQ==?= <ivan@example.ru>, b@example.ru"
    )
    assert addresses(value) == [
        {"name": "Иванов, Иван", "address": "ivan@example.ru"},
        {"name": None, "address": "b@example.ru"},
    ]


def test_date_keeps_sender_offset_and_falls_back():
    with_date = parse(raw(b"Date: Tue, 18 Nov 2026 10:23:45 +0300", b"", b"x"))
    assert read_headers(with_date).date.isoformat() == "2026-11-18T10:23:45+03:00"

    unknown_zone = parse(raw(b"Date: Tue, 18 Nov 2026 10:23:45 -0000", b"", b"x"))
    assert read_headers(unknown_zone).date.isoformat() == "2026-11-18T10:23:45+00:00"

    fallback = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    broken = parse(raw(b"Date: not a date", b"", b"x"))
    assert read_headers(broken, fallback).date == fallback


def test_message_ids_and_references():
    message = parse(
        raw(
            b"Message-ID: <c@x>",
            b"In-Reply-To: <b@x> (comment)",
            b"References: <a@x>\r\n <b@x>",
            b"",
            b"x",
        )
    )
    headers = read_headers(message)
    assert (headers.message_id, headers.in_reply_to, headers.references) == (
        "<c@x>",
        "<b@x>",
        ["<a@x>", "<b@x>"],
    )


def test_message_dict_modes():
    message = make_raw(
        subject="Квитанция",
        body="Текст " * 100,
        attachments=[("Квитанция.pdf", "application/pdf", b"%PDF-1.4")],
        message_id="<m1@example.ru>",
    )
    full = message_dict(message, folder="INBOX", uid=5, uidvalidity=7, seen=True)
    summary = message_dict(
        message, folder="INBOX", uid=5, uidvalidity=7, content="snippet"
    )
    head = message_dict(
        message.split(b"\r\n\r\n")[0],
        folder="INBOX",
        uid=5,
        uidvalidity=7,
        content="headers",
    )

    assert full["text"].startswith("Текст Текст")
    assert full["attachments"][0]["filename"] == "Квитанция.pdf"
    assert full["seen"] is True and full["message_id"] == "<m1@example.ru>"
    assert "text" not in summary and len(summary["snippet"]) == 200
    assert summary["snippet"].endswith("…")
    assert head["snippet"] is None and head["attachments"] is None
    assert head["subject"] == "Квитанция"
