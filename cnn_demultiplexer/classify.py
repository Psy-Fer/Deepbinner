
import sys
import pathlib
import h5py
import math
import numpy as np
from keras.models import load_model
from .load_fast5s import find_all_fast5s, get_read_id_and_signal
from .trim_signal import normalise


def classify(args):
    input_type = determine_input_type(args.input)
    if input_type == 'training_data' and len(args.model) == 2:
        sys.exit('Error: training data can only be classified using a single model')

    start_model, start_input_size, output_size = load_trained_model(args.model[0])
    end_model, end_input_size, end_output_size = None, None, None
    if len(args.model) == 2:
        end_model, end_input_size, end_output_size = load_trained_model(args.model[1])
        if output_size != end_output_size:
            sys.exit('Error: two models have different number of barcode classes')

    print_output_header(args.verbose, end_model)

    if input_type == 'directory':
        classify_fast5_files(find_all_fast5s(args.input, verbose=True),
                             start_model, start_input_size, end_model, end_input_size,
                             output_size, args)
    elif input_type == 'single_fast5':
        classify_fast5_files([args.input],
                             start_model, start_input_size, end_model, end_input_size,
                             output_size, args)
    elif input_type == 'training_data':
        classify_training_data(args.input, start_model, start_input_size, args)
    else:
        assert False


def load_trained_model(model_file):
    print('', file=sys.stderr)
    if not pathlib.Path(model_file).is_file():
        sys.exit('Error: {} does not exist'.format(model_file))
    print('Loading {} neural network... '.format(model_file), file=sys.stderr, end='', flush=True)
    model = load_model(model_file)
    print('done', file=sys.stderr)
    try:
        assert len(model.inputs) == 1
        input_shape = model.inputs[0].shape
        output_shape = model.outputs[0].shape
        input_size = int(input_shape[1])
        output_size = int(output_shape[1])
        assert input_size > 10
        assert input_shape[2] == 1
    except (AssertionError, IndexError):
        sys.exit('Error: model input has incorrect shape - are you sure that {} is a valid '
                 'model file?'.format(model_file))
    return model, input_size, output_size


def classify_fast5_files(fast5_files, start_model, start_input_size, end_model, end_input_size,
                         output_size, args):
    print('', file=sys.stderr)
    # TO DO: progress bar

    using_read_ends = end_model is not None

    for fast5_batch in chunker(fast5_files, args.batch_size):
        read_ids, signals = [],  []

        for i, fast5_file in enumerate(fast5_batch):
            read_id, signal = get_read_id_and_signal(fast5_file)
            read_ids.append(read_id)
            signals.append(signal)

        start_calls, start_probs = call_batch(fast5_batch, start_input_size, output_size, read_ids,
                                              signals, start_model, args, 'start')
        if using_read_ends:
            end_calls, end_probs = call_batch(fast5_batch, end_input_size, output_size, read_ids,
                                              signals, end_model, args, 'end')
        else:
            end_calls, end_probs = None, None

        for i, read_id in enumerate(read_ids):
            if using_read_ends:
                final_barcode_call = combine_calls(start_calls[i], end_calls[i], args.require_both)
            else:
                final_barcode_call = start_calls[i]
            output = [read_id, final_barcode_call]

            if args.verbose:
                output += ['%.2f' % x for x in start_probs[i]]
                if using_read_ends:
                    output.append(start_calls[i])
                    output += ['%.2f' % x for x in end_probs[i]]
                    output.append(end_calls[i])
            print('\t'.join(output))


def classify_training_data(input_file, model, input_size, args):
    print('', file=sys.stderr)
    quit()

    # labels = model.predict(signals, batch_size=batch_size)


def determine_input_type(input_file_or_dir):
    path = pathlib.Path(input_file_or_dir)
    if path.is_dir():
        return 'directory'
    if not path.is_file():
        sys.exit('Error: {} is neither a file nor a directory'.format(input_file_or_dir))
    try:
        f = h5py.File(input_file_or_dir, 'r')
        f.close()
        return 'single_fast5'
    except OSError:
        pass
    with open(input_file_or_dir) as f:
        first_line = f.readline()
    try:
        parts = first_line.split('\t')
        _ = int(parts[0])
        signals = [int(x) for x in parts[1].split(',')]
        assert len(signals) > 10
        return 'training_data'
    except (AssertionError, ValueError, IndexError):
        sys.exit('Error: could not determine input type')


def chunker(seq, size):
    return (seq[pos:pos + size] for pos in range(0, len(seq), size))


def print_output_header(verbose, end_model):
    if not verbose:
        print('\t'.join(['read_ID', 'barcode_call']))
    elif end_model is None:  # just doing start-read classification
        print('\t'.join(['read_ID', 'barcode_call',
                         'none', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12']))
    else:  # doing both start-read and end-read classification
        print('\t'.join(['read_ID', 'barcode_call',
                         'start_none', 'start_1', 'start_2', 'start_3', 'start_4', 'start_5',
                         'start_6', 'start_7', 'start_8', 'start_9', 'start_10', 'start_11',
                         'start_12', 'start_barcode_call',
                         'end_none', 'end_1', 'end_2', 'end_3', 'end_4', 'end_5', 'end_6', 'end_7',
                         'end_8', 'end_9', 'end_10', 'end_11', 'end_12', 'end_barcode_call']))


def get_barcode_call_from_probabilities(probabilities, score_diff_threshold):
    probabilities = list(enumerate(probabilities))  # make into tuples (barcode, prob)
    probabilities = sorted(probabilities, key=lambda x: x[1], reverse=True)
    best, second_best = probabilities[0], probabilities[1]
    if best[0] == 0:
        return 'none'
    score_diff = best[1] - second_best[1]
    if score_diff >= score_diff_threshold:
        return str(best[0])
    else:
        return 'none'


def combine_calls(start_call, end_call, require_both):
    if start_call == end_call:
        return start_call
    if require_both:
        return 'none'
    if start_call == 'none':
        return end_call
    if end_call == 'none':
        return start_call
    return 'none'


def call_batch(fast5_batch, input_size, output_size, read_ids, signals, model, args, side):
    probabilities = []
    for _ in fast5_batch:
        probabilities.append([0.0] * output_size)

    step_size = input_size // 2
    steps = int(args.scan_size / step_size)

    # TO DO: check to make sure this will work earlier in the code and quit with a nice error
    # message if not so.
    assert steps * step_size == args.scan_size

    for s in range(steps):
        sig_start = s * step_size
        sig_end = sig_start + input_size

        input_signals = np.empty([len(fast5_batch), input_size], dtype=float)
        for i, signal in enumerate(signals):
            if side == 'start':
                input_signal = signal[sig_start:sig_end]
            else:
                assert side == 'end'
                a = max(len(signal) - sig_start, 0)
                b = max(len(signal) - sig_end, 0)
                input_signal = signal[a:b]

            input_signal = normalise(input_signal)
            if len(input_signal) < input_size:
                pad_size = input_size - len(input_signal)
                if side == 'start':
                    input_signal = np.pad(input_signal, (0, pad_size), 'constant')
                else:
                    input_signal = np.pad(input_signal, (pad_size, 0), 'constant')
            input_signals[i] = input_signal

        input_signals = np.expand_dims(input_signals, axis=2)
        labels = model.predict(input_signals, batch_size=args.batch_size)

        for i, read_id in enumerate(read_ids):
            probabilities[i] = [max(x) for x in zip(list(labels[i]),
                                                    probabilities[i])]

    # Make all the probabilities sum to 1.
    for i, read_id in enumerate(read_ids):
        total = sum(probabilities[i])
        probabilities[i] = [x / total for x in probabilities[i]]

    barcode_calls = []
    for i, read_id in enumerate(read_ids):
        barcode_calls.append(get_barcode_call_from_probabilities(probabilities[i], args.score_diff))

    return barcode_calls, probabilities