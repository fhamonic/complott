import csv

def parse_csv_as_dict(
    file_path,
    columns,
    index_column,
    indices=None,
    columns_types=None,
    delimiter=";",
    first_data_row=1,
    last_data_row=None,
):
    with open(file_path, "r") as file:
        lines = [line.rstrip() for line in file]
        if last_data_row is None:
            last_data_row = len(lines)
        if columns_types is None:
            columns_types = [str for _ in columns]
        data_rows = [lines[i] for i in range(first_data_row - 1, last_data_row)]
        rows = list(csv.DictReader(data_rows, fieldnames=columns, delimiter=delimiter))
        if indices is None:
            return {
                row[index_column]: {
                    k: t(row[k])
                    for k, t in zip(columns, columns_types)
                    if k != index_column
                }
                for row in rows
            }
        else:
            return {
                i: {
                    k: t(row[k])
                    for k, t in zip(columns, columns_types)
                    if k != index_column
                }
                for row, i in zip(rows, indices)
            }