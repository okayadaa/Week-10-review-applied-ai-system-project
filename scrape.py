import subprocess

KERAS_URL = "https://keras.io/api/"
OUTPUT_DIR = "data/keras_docs"


def scrape():
    subprocess.run(
        [
            "wget",
            "--mirror",
            "--convert-links",
            "--adjust-extension",
            "--page-requisites",
            "--no-parent",
            "-P",
            OUTPUT_DIR,
            KERAS_URL,
        ],
        check=True,
    )
