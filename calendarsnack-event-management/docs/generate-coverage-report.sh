
#!/bin/bash
if ! which coverage; then
    echo 'Error: coverage missing, please run `pip install coverage` first.'
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

coverage html
(
    echo "---"
    echo "title: Coverage report"
    echo "---"
    cat "${SCRIPT_DIR}/content/en/coverage/index.html"
) >> "${SCRIPT_DIR}/content/en/coverage/_index.html"
mv "${SCRIPT_DIR}/content/en/coverage/_index.html" \
    "${SCRIPT_DIR}/content/en/coverage/index.html"
