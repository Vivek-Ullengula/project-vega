from decimal import Decimal

from adapters.aws.dynamodb import DynamoDBAdapter


def test_to_dynamodb_value_converts_nested_floats_to_decimal():
    value = {
        "messages": [
            {
                "role": "assistant",
                "citations": [{"source_id": "S1", "score": 0.42}],
            }
        ]
    }

    converted = DynamoDBAdapter._to_dynamodb_value(value)

    assert converted["messages"][0]["citations"][0]["score"] == Decimal("0.42")
