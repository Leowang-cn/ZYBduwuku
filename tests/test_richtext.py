from backend.richtext import RICH_PREFIX, clean_rich


def test_clean_rich_preserves_safe_media_size_and_alignment():
    source = RICH_PREFIX + '<img src="https://example.com/a.png" style="width: 256px; text-align: center"><figure style="text-align: right"><video src="https://example.com/a.mp4" style="width: 343px"></video></figure>'
    result = clean_rich(source)

    assert 'style="width: 256px; text-align: center"' in result
    assert '<figure style="text-align: right"><video src="https://example.com/a.mp4" style="width: 343px" controls preload="metadata"></video></figure>' in result


def test_clean_rich_preserves_inline_colors_and_highlight():
    source = RICH_PREFIX + '<span style="background-color: rgb(255, 240, 0); color: #123456">colored</span><mark data-highlight="true">important</mark>'
    result = clean_rich(source)

    assert 'background-color: rgb(255, 240, 0)' in result
    assert 'color: #123456' in result
    assert '<mark' in result and 'important</mark>' in result


def test_clean_rich_drops_unsafe_media_styles():
    source = RICH_PREFIX + '<img src="https://example.com/a.png" style="width: 3000px; position: fixed; text-align: center">'

    result = clean_rich(source)

    assert 'width' not in result
    assert 'position' not in result
    assert 'text-align: center' in result


def test_clean_rich_preserves_callout_without_unsafe_attributes():
    result = clean_rich(RICH_PREFIX + '<aside onclick="alert(1)" style="position:fixed">提示</aside>')
    assert result == RICH_PREFIX + '<aside>提示</aside>'
