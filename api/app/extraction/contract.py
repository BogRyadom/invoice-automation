# Extraction contract (docs/SPEC.md section 6). The LLM returns values exactly as printed,
# code normalizes numbers, dates and currency. There is no confidence field by design.

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

DocumentType = Literal["invoice", "credit_note", "other"]


class TaxLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    amount_raw: str


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None
    quantity_raw: str | None
    unit_price_raw: str | None
    amount_raw: str | None


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    vendor_name_raw: str | None
    vendor_tax_id_raw: str | None
    invoice_number_raw: str | None
    invoice_date_raw: str | None
    due_date_raw: str | None
    currency_raw: str | None
    subtotal_raw: str | None
    discount_raw: str | None
    shipping_raw: str | None
    tax_lines: list[TaxLine]
    tax_inclusive_note_raw: str | None
    total_raw: str | None
    line_items: list[LineItem]


def strict_json_schema(model: type[BaseModel] = Extraction) -> dict[str, Any]:
    """Schema in the strict structured-output subset: refs inlined, all fields required."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def convert(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            return convert(definitions[node["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in node:
            options = [convert(option) for option in node["anyOf"]]
            if all(set(option) == {"type"} for option in options):
                return {"type": [option["type"] for option in options]}
            return {"anyOf": options}
        result = {key: value for key, value in node.items() if key not in ("title", "default")}
        if result.get("type") == "object":
            properties = {name: convert(sub) for name, sub in node["properties"].items()}
            result.update(
                properties=properties, required=list(properties), additionalProperties=False
            )
        if result.get("type") == "array":
            result["items"] = convert(node["items"])
        return result

    return convert(schema)
