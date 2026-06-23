from vortnotes.sanitizer import sanitize_note_html


def test_sanitizer_strips_script_tags():
    html = "<p>ok</p><script>alert(1)</script>"
    out = sanitize_note_html(html)
    assert "script" not in out.lower()
    # The tag is stripped; remaining text is harmless.
    assert "<p>ok</p>" in out


def test_sanitizer_drops_data_uri_images():
    html = '<p><img src="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="></p>'
    out = sanitize_note_html(html)
    assert "<img" not in out.lower()


def test_sanitizer_allows_upload_images():
    html = '<p><img src="/uploads/abc/test.png" width="16" height="16"></p>'
    out = sanitize_note_html(html)
    assert "/uploads/abc/test.png" in out


def test_sanitizer_allows_youtube_iframe_only():
    ok = '<iframe src="https://www.youtube.com/embed/xyz" width="560" height="315"></iframe>'
    bad = '<iframe src="https://evil.example/embed/xyz"></iframe>'
    assert "youtube.com" in sanitize_note_html(ok)
    assert "iframe" not in sanitize_note_html(bad).lower()
