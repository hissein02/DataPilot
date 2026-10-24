"""A deliberately narrow allowlist for model-produced analytical SQL."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class UnsafeQuery(ValueError):
    pass


_SAFE_FUNCTIONS = {
    "ABS",
    "AVG",
    "CAST",
    "CEIL",
    "CEILING",
    "COALESCE",
    "COUNT",
    "DATE_TRUNC",
    "DAY",
    "FLOOR",
    "IFNULL",
    "LENGTH",
    "LOWER",
    "MAX",
    "MEDIAN",
    "MIN",
    "MODE",
    "MONTH",
    "NULLIF",
    "ROUND",
    "STDDEV",
    "STDDEV_POP",
    "STDDEV_SAMP",
    "STRFTIME",
    "SUBSTR",
    "SUBSTRING",
    "SUM",
    "TRIM",
    "TRY_CAST",
    "UPPER",
    "YEAR",
}


def validate_sql(sql: str, allowed_tables: set[str]) -> str:
    """Allow one SELECT over the uploaded table and reject unsafe SQL."""
    if not sql or len(sql) > 20_000:
        raise UnsafeQuery("SQL is empty or too long.")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except SqlglotError as exc:
        raise UnsafeQuery("SQL could not be parsed.") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsafeQuery("Only one SELECT statement is allowed.")
    tree = statements[0]
    if tree is None:
        raise UnsafeQuery("SQL could not be parsed.")
    for limit in tree.find_all(exp.Limit):
        if isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
            if int(limit.expression.this) == 0:
                raise UnsafeQuery(
                    "Columns are already available from the uploaded profile; do not use LIMIT 0 to inspect them."
                )

    # A SELECT can contain writes or external readers in nested expressions.
    forbidden = (
        exp.Command,
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Copy,
        exp.Merge,
        exp.Attach,
        exp.Pragma,
        exp.Into,
    )
    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    workspace_referenced = False
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise UnsafeQuery("SQL contains an unsupported operation.")
        if isinstance(node, exp.Table):
            if node.db or node.catalog or not isinstance(node.this, exp.Identifier):
                raise UnsafeQuery("Qualified tables and table functions are not allowed.")
            if node.name.lower() not in allowed_tables and node.name.lower() not in cte_names:
                raise UnsafeQuery(f"Table {node.name!r} is not in this workspace.")
            if node.name.lower() in allowed_tables and node.name.lower() not in cte_names:
                workspace_referenced = True
        if isinstance(node, exp.Func):
            function_name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if function_name.upper() not in _SAFE_FUNCTIONS:
                raise UnsafeQuery(f"Function {function_name} is not allowed.")
    if not workspace_referenced:
        raise UnsafeQuery("A workspace table is required.")
    try:
        return tree.sql(dialect="duckdb")
    except SqlglotError as exc:
        raise UnsafeQuery("SQL could not be rendered safely.") from exc
