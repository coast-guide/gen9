"""A connector's server asking the person (elicitation.py): which interrupt it is, and the
answers checked against what was asked."""

import warnings

import pytest

from gen9_agent import elicitation
from gen9_agent.runs.control import _checked
from gen9_agent.runs.executor import kind_of

pytestmark = pytest.mark.asyncio

FORM = {
    "key": "trip",
    "message": "Where to?",
    "mode": "form",
    "requested_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "minLength": 2},
            "nights": {"type": "integer", "minimum": 1, "maximum": 30},
            "email": {"type": "string", "format": "email"},
            "window": {"type": "boolean"},
            "class": {
                "type": "string",
                "oneOf": [
                    {"const": "eco", "title": "Economy"},
                    {"const": "biz", "title": "Business"},
                ],
            },
            "extras": {
                "type": "array",
                "items": {"enum": ["wifi", "meals", "lounge"]},
                "maxItems": 2,
            },
        },
        "required": ["city", "nights"],
    },
}
URL = {
    "key": "calendar",
    "message": "Connect your calendar",
    "mode": "url",
    "url": "https://calendar.example.com/connect",
}
REQUEST = {
    "type": "mcp_elicitation",
    "tool_name": "travel__plan_trip",
    "requests": [FORM, URL],
}


async def test_the_interrupt_is_an_elicitation_and_names_its_connector():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langchain.mcp.elicitation import ELICITATION_INTERRUPT_TYPE
    assert elicitation.INTERRUPT_TYPE == ELICITATION_INTERRUPT_TYPE
    assert kind_of(REQUEST) == elicitation.KIND
    assert elicitation.connector_of(REQUEST["tool_name"]) == "travel"


async def test_a_filled_form_and_a_consented_url_resume_the_call():
    checked = _checked(
        elicitation.KIND,
        REQUEST,
        {
            "responses": {
                "trip": {
                    "action": "accept",
                    "content": {
                        "city": "Lisbon",
                        "nights": 3.0,
                        "class": "biz",
                        "extras": ["wifi"],
                        "window": True,
                        "email": "a@b.co",
                    },
                },
                "calendar": {"action": "accept", "content": {"sneaky": "data"}},
            }
        },
    )
    assert checked == {
        "responses": {
            "trip": {
                "action": "accept",
                "content": {
                    "city": "Lisbon",
                    "nights": 3,
                    "class": "biz",
                    "extras": ["wifi"],
                    "window": True,
                    "email": "a@b.co",
                },
            },
            # A URL's answer carries nothing, whatever was sent
            "calendar": {"action": "accept"},
        }
    }


async def test_declining_and_cancelling_carry_no_content():
    checked = elicitation.check_responses(
        REQUEST,
        {
            "trip": {"action": "decline", "content": {"city": "x"}},
            "calendar": {"action": "cancel"},
        },
    )
    assert checked == {"trip": {"action": "decline"}, "calendar": {"action": "cancel"}}


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ({"nights": 3}, "required: city"),
        ({"city": "Lisbon", "nights": 0}, "at least 1"),
        ({"city": "Lisbon", "nights": 2.5}, "whole number"),
        ({"city": "Lisbon", "nights": True}, "a number"),
        ({"city": "Lisbon", "nights": 2, "class": "first"}, "one of the choices"),
        (
            {"city": "Lisbon", "nights": 2, "extras": ["wifi", "meals", "lounge"]},
            "number of choices",
        ),
        ({"city": "Lisbon", "nights": 2, "extras": ["spa"]}, "some of the choices"),
        ({"city": "Lisbon", "nights": 2, "email": "not-an-email"}, "email"),
        ({"city": "Lisbon", "nights": 2, "password": "x"}, "not in the form"),
        ({"city": "L", "nights": 2}, "length"),
    ],
)
async def test_a_form_that_doesnt_fit_its_schema_is_refused(content, why):
    with pytest.raises(ValueError, match=why):
        elicitation.check_responses(
            REQUEST,
            {
                "trip": {"action": "accept", "content": content},
                "calendar": {"action": "decline"},
            },
        )


async def test_every_request_is_answered_and_only_those():
    with pytest.raises(ValueError, match="every request"):
        elicitation.check_responses(REQUEST, {"trip": {"action": "decline"}})
    with pytest.raises(ValueError, match="accept, decline or cancel"):
        elicitation.check_responses(
            REQUEST, {"trip": {"action": "maybe"}, "calendar": {"action": "decline"}}
        )
    with pytest.raises(ValueError, match="takes responses"):
        _checked(elicitation.KIND, REQUEST, {"answers": ["x"]})


async def test_the_request_names_its_connector_and_keeps_the_forms_order():
    value = {
        "type": "mcp_elicitation",
        "tool_name": "plan_trip",
        "requests": [FORM, URL],
    }
    calls = [
        {"name": "write_todos", "args": {}},
        {"name": "travel__plan_trip", "args": {}, "id": "c1"},
    ]
    described = elicitation.described(value, calls)
    assert described["connector"] == "travel"
    assert described["requests"][0]["order"] == [
        "city",
        "nights",
        "email",
        "window",
        "class",
        "extras",
    ]
    assert "order" not in described["requests"][1]
    # With no call to go by, the tool's own name stands in
    assert elicitation.described(value, [])["connector"] == "plan_trip"
