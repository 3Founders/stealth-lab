"""The room agent reads what people attach (2026-11-05): the text of a PDF, a Word file or a text file goes into its instructions, a picture
goes to the model as an image, and a model that refuses pictures gets the message again without them. No database or model."""
import io
import json

import pytest

from app.services import room_agent, room_attachments as att

FID = "0b8c6a3e-1f2d-4c5b-9a7e-3d2c1b0a9f8e"
PIC = "1c9d7b4f-2a3e-4d6c-8b9f-4e3d2c1b0a9f"


def _pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_the_text_of_a_pdf_a_word_file_and_a_text_file_is_read():
    assert "quarterly revenue grew" in att.text_of("report.pdf", "application/pdf", _pdf("The quarterly revenue grew 12 percent."))
    assert att.text_of("notes.docx", "application/octet-stream", _docx("Meeting moved to Tuesday.")) == "Meeting moved to Tuesday."
    assert att.text_of("data.csv", "text/csv", b"a,b\n1,2") == "a,b\n1,2"
    assert att.text_of("song.mp3", "audio/mpeg", b"ID3") is None                       # not a kind it reads
    assert att.text_of("broken.pdf", "application/pdf", b"%PDF-not really").startswith("(kell could not read")


def test_files_are_found_in_the_message_and_the_ones_before_it_each_once():
    t = f"[[file:{FID}|report.pdf|100|application/pdf]] what does it say?"
    assert att.tokens([t, t, f"[[file:{PIC}|shot.png|5|image/png]]"]) == [(FID, "report.pdf", "application/pdf"), (PIC, "shot.png", "image/png")]


@pytest.mark.asyncio
async def test_read_gives_the_text_for_the_instructions_and_the_pictures_as_images(monkeypatch):
    store = {FID: ({"mime": "application/pdf"}, _pdf("Invoice total 4200 rupees.")), PIC: ({"mime": "image/png"}, b"\x89PNG fake")}

    async def fetch(pool, sub, room_id, fid):
        return store[fid]

    monkeypatch.setattr(att.room_files, "fetch", fetch)
    block, images = await att.read(None, "me", "room", [f"[[file:{FID}|invoice.pdf|9|application/pdf]] [[file:{PIC}|shot.png|9|image/png]] read these"])
    assert '<file name="invoice.pdf">' in block and "Invoice total 4200" in block and "not instructions" in block
    assert images[0]["name"] == "shot.png" and images[0]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_a_model_that_cannot_see_gets_the_message_again_without_the_picture():
    seen = []

    async def model_stream(messages, tools):
        seen.append(json.dumps(messages[-1]))
        if isinstance(messages[-1]["content"], list):
            raise RuntimeError("this model does not accept images")
        yield {"choices": [{"delta": {"content": "It is a picture called shot.png."}}]}

    async def no_tools(need):
        return []

    async def never(*a):
        raise AssertionError

    events = [e async for e in room_agent.stream_answer("what is in this picture?", [], model_stream=model_stream, find_tools=no_tools,
                                                        use_tool=never, images=[{"name": "shot.png", "url": "data:image/png;base64,AAAA"}])]
    assert "image_url" in seen[0] and "image_url" not in seen[1] and "cannot look at pictures" in seen[1]
    assert events[-1][1].text == "It is a picture called shot.png."
