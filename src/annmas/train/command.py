import logging

import click
import click_log
import tqdm

import multiprocessing as mp
import concurrent.futures

from ..utils.model import *

logger = logging.getLogger(__name__)
click_log.basic_config(logger)


@click.command(name="train")
@click_log.simple_verbosity_option(logger)
@click.option("-n", "--num-training-samples", type=int, default=10, show_default=True,
              help="number of training samples to use")
@click.option("-i", "--max-training-iterations", type=int, default=5, show_default=True,
              help="number of training iterations to use")
@click.option("-t", "--threads", type=int, default=1, show_default=True, help="number of threads to use (0 for all)")
@click.option("-o", "--output-yaml", required=True, type=click.Path(exists=False), help="trained model")
@click.argument('training-bam', type=click.Path(exists=True))
def main(num_training_samples, max_training_iterations, threads, output_yaml, training_bam):
    """Train transition and emission probabilities of model on real data"""
    logger.info(f"annmas: train started")

    threads = mp.cpu_count() if threads <= 0 or threads > mp.cpu_count() else threads
    logger.info("Running with %d thread(s)", threads)

    m = build_default_model()
    training_seqs = load_training_seqs(m, num_training_samples, threads, training_bam)

    logger.info("Loaded %d training sequences", len(training_seqs))

    logger.info("Starting training...", len(training_seqs))
    improvement, history = m.fit(sequences=training_seqs,
                                 max_iterations=max_training_iterations,
                                 stop_threshold=1e-1,
                                 return_history=True,
                                 verbose=True,
                                 n_jobs=threads)

    with open(output_yaml, "w") as model_file:
        print(improvement.to_yaml(), file=model_file)

    logger.info("annmas: train finished")


def load_training_seqs(m, num_training_samples, threads, training_bam):
    training_seqs = []
    raw_reads = []
    pysam.set_verbosity(0)  # silence message about the .bai file not being found
    with pysam.AlignmentFile(training_bam, "rb", check_sq=False, require_index=False) as bam_file:
        for r in bam_file:
            raw_reads.append(r)

            if len(raw_reads) > num_training_samples:
                break
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor, \
            tqdm.tqdm(desc="Progress", unit=" reads", colour="green", file=sys.stdout) as pbar:

        future_to_segmented_read = {executor.submit(select_read, r, m): r for r in raw_reads}

        for future in concurrent.futures.as_completed(future_to_segmented_read):
            read = future_to_segmented_read[future]
            try:
                logp, seq = future.result()
                training_seqs.append(list(seq))
            except Exception as ex:
                logger.error('%r generated an exception: %s', read, ex)

            pbar.update(1)
    return training_seqs


def select_read(read, model):
    flogp = -math.inf
    fseq = None

    # Use the untrained model to determine if we should add this training
    # example in the forward or reverse-complement orientation.
    for seq in [read.query_sequence, reverse_complement(read.query_sequence)]:
        logp, ppath = annotate(model, seq, smooth_islands=True)

        if logp > flogp:
            flogp = logp
            fseq = seq

    return flogp, fseq