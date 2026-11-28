from marshmallow import Schema, fields


class ProfileSchema(Schema):
    username = fields.Str()
    created = fields.DateTime(attribute="created_at", dump_only=True)
    updated = fields.DateTime()


class ArticleSchema(Schema):
    title = fields.Str()
    author = fields.Nested(ProfileSchema)
    parent = fields.Nested("self", exclude=("parent",), load_only=True)  # BREAK
    tags = fields.List(fields.Str())
