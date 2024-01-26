#!/usr/bin/env python3
# %%
from pathlib import Path
from absl import logging
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
import os
import pandas as pd
import shutil
import time
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from typing import Union
import plaseek.tools.foldseek
import plaseek.tools.blastdbcmd
import plaseek.tools.tblastn

logging.set_verbosity(logging.INFO)


def check_binaries_available(
    parallel_binary_path: str, tblastn_binary_path: str, foldseek_binary_path: str
) -> None:
    """Check if binaries are available."""
    if not Path(parallel_binary_path).exists():
        raise FileNotFoundError("foldseek not found. Please set PATH to foldseek.")
    if not Path(tblastn_binary_path).exists():
        raise FileNotFoundError("tblastn not found. Please set PATH to tblastn.")
    if not Path(foldseek_binary_path).exists():
        raise FileNotFoundError("parallel not found. Please set PATH to parallel.")


def filtering_m8file(
    file: Union[str, Path], eval_threshold: float = 1e-10
) -> pd.DataFrame:
    """Filtering Foldseek result file in M8 format.
    filter by e-value < 1e-10
    The header is "query,theader,pident,alnlen,mismatch,gapopen,qstart,qend,tstart,tend,prob,evalue,bits,qlen,tlen,qaln,taln,tca,tseq,taxid,taxname".
    """
    m8file = Path(file)
    df = pd.read_csv(
        m8file,
        sep="\t",
        names=[
            "query",
            "theader",
            "pident",
            "alnlen",
            "mismatch",
            "gapopen",
            "qstart",
            "qend",
            "tstart",
            "tend",
            "prob",
            "evalue",
            "bits",
            "qlen",
            "tlen",
            "qaln",
            "taln",
            "tca",
            "tseq",
            "taxid",
            "taxname",
        ],
    )
    df_filtered = df[df["evalue"] < eval_threshold]
    return df_filtered


def remove_duplicates(hits: pd.DataFrame) -> list[SeqRecord]:
    """Remove duplicate sequences from a fasta file.

    Args:
        hits (pd.DataFrame):
    Returns:
        list[SeqRecord]: List of SeqRecord objects.
    """
    seen = set()
    nodups = []
    for _, row in hits.iterrows():
        if row["tseq"] not in seen:
            nodups.append(
                SeqRecord(
                    id=row["theader"],
                    name=row["theader"],
                    seq=Seq(row["tseq"]),
                    description=f"pident={row['pident']}, evalue={row['evalue']} taxid={row['taxid']}, taxname={row['taxname']}, ",
                )
            )
            seen.add(row["tseq"])
    return nodups


def filtering_by_pident(
    infile: str,
    pident_threshold: float = 98.0,
    sort_values: str = "saccver",
) -> pd.DataFrame:
    """Collect plasmid accession ID from tblastn output file.
    The pident value should be greater than or equal to 98.0 (default).
    """
    df = pd.read_csv(infile, sep="\t", header=0)

    df_filtered = df[df["pident"] >= pident_threshold]
    # sort by sort_values (default: saccver)
    df_filtered_sorted = df_filtered.sort_values(by=[f"{sort_values}"])
    return df_filtered_sorted


def main():
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)

    binary_group = parser.add_argument_group("binary arguments", "")
    binary_group.add_argument(
        "--parallel-binary-path",
        type=str,
        default=shutil.which("parallel"),
        help="Path to the parallel executable.",
    )
    binary_group.add_argument(
        "--tblastn-binary-path",
        type=str,
        default=shutil.which("tblastn"),
        help="Path to the tblastn executable.",
    )
    binary_group.add_argument(
        "--foldseek-binary-path",
        type=str,
        default=shutil.which("foldseek"),
        help="Path to the Foldseek executable.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        default=None,
        help="Path to the input file. pdb or foldseek tsv file are acceptable.",
    )
    parser.add_argument(
        "--foldseek-db-path",
        type=str,
        default=os.getenv("FOLDSEEKDB"),
        help="Path to foldseek database.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="%(prog)s 0.0.1",
    )
    tblastn_group = parser.add_argument_group("tblastn arguments", "")
    tblastn_group.add_argument(
        "--target-sequence-db-path",
        type=str,
        default=None,
        help="Path to the target sequence database file for tblastn.",
    )
    tblastn_group.add_argument(
        "--evalue",
        type=float,
        default=1e-100,
        help="E-value threshold for tblastn.",
    )
    tblastn_group.add_argument(
        "--block",
        type=int,
        default=3000,
        help="Block size for tblastn.",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        help="Number of parallel jobs.",
    )
    parser.add_argument(
        "-o",
        "--outfile-path",
        type=str,
        default=None,
        help="Path to the output file.",
    )

    args = parser.parse_args()

    check_binaries_available(
        args.parallel_binary_path, args.tblastn_binary_path, args.foldseek_binary_path
    )

    input = Path(args.input)
    if input.suffix == ".pdb":
        foldseek_m8file = Path(f"{input.stem}.m8")
        plaseek.tools.foldseek.run_foldseek(
            pdbfile=input,
            foldseek_binary_path=args.foldseek_binary_path,
            foldseek_db_path=args.foldseek_db_path,
            outtsvfile=foldseek_m8file,
            jobs=args.jobs,
        )
    elif input.suffix == ".m8":
        foldseek_m8file = input
    else:
        raise ValueError("Invalid input file suffix: the suffix must be .pdb or .m8.")

    filtered_foldseekhits: pd.DataFrame = filtering_m8file(foldseek_m8file)

    nodup_fasta = f"{input.stem}_nodup.fasta"
    with open(nodup_fasta, "w") as fh:
        SeqIO.write(remove_duplicates(filtered_foldseekhits), fh, "fasta")

    tblastn_result = f"{input.stem}_tblastn.tsv"
    start = time.perf_counter()
    plaseek.tools.tblastn.run_tblastn(
        db=args.target_sequence_db_path,
        input_fasta=nodup_fasta,
        outfile=tblastn_result,
        block=args.block,
        tblastn_binary_path=args.tblastn_binary_path,
        parallel_binary_path=args.parallel_binary_path,
        evalue=args.evalue,
    )
    duration = time.perf_counter() - start
    print(f"{duration} seconds.")

    filtered_tblastn = filtering_by_pident(tblastn_result, pident_threshold=98.0)

    plaseek.tools.blastdbcmd.run_blastdbcmd(
        blastdbcmd_binary_path=args.blastdbcmd_binary_path,
        parallel_binary_path=args.parallel_binary_path,
        outfile=args.outfile_path,
        db=args.target_sequence_db_path,
        df=filtered_tblastn,
        jobs=args.jobs,
    )


if __name__ == "__main__":
    main()