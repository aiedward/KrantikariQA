"""
    Author: geraltofrivia

    This script takes the json created by the pre-processing module and converts them into X and Y for the network.
    This X and Y depend on the network architecture that we're following so is expected to change now and then.

    Done:
        -> Embed a sentence

"""
import json
import os
import pickle
import random
import traceback
import warnings
import numpy as np
from pprint import pprint
from gensim import models
from progressbar import ProgressBar

from utils import embeddings_interface
from utils import dbpedia_interface as db_interface
from utils import natural_language_utilities as nlutils

DEBUG = True
pADDTYPE = 0.35
EMBEDDING_DIM = 300
MAX_FALSE_PATHS = 100

# Set a seed for deterministic randomness
random.seed(42)

# Better warning formatting. Ignore
def better_warning(message, category, filename, lineno, file=None, line=None):
    return ' %s:%s: %s:%s\n' % (filename, lineno, category.__name__, message)


warnings.formatwarning = better_warning
if DEBUG:
    warnings.warn(" DEBUG macro is enabled. Expect cluttered console!")

# Initialize DBpedia
dbp = db_interface.DBPedia(_verbose=True, caching=True)


def compute_true_labels(_question, _truepath, _falsepaths):
    """
        Function to compute the training labels corresponding to the paths given in training data.

        Logic: (for now (now is 4th Dec, 2017))
            return a static array of [ 1, 0, 0, ... 20 times ]

    :param _question: np array ( x, 300)
    :param _truepath: np array ( x, 300)
    :param _falsepaths: list of np arrays (20, x_i, 300)
    :return: np array, len(_falsepaths) + 1
    """
    return np.asarray([1] + [0 for x in range(20)])


def parse(_raw):
    """
    -> parse and vectorize the question.
    -> parse and vectorize the correct path.
    -> create false paths.
        ->
    -> vectorize the false paths.

    :param _raw: A dict. (See ./resources/structure.json)
    :return: None

    @TODO: Generalize this for more than one topic entity.
    """
    if len(_raw[u'entity']) == 1:

        # Get the question
        question = _raw[u'corrected_question']

        # Tokenize the question
        question = nlutils.tokenize(question)

        # Now, embed the question.
        id_question = embeddings_interface.vocabularize(question, _report_unks=False)

        # Make the correct path
        entity = _raw[u'entity'][0]
        entity_sf = nlutils.tokenize(dbp.get_label(entity), _ignore_brackets=False)  # @TODO: multi-entity alert
        path_sf = []
        for x in _raw[u'path']:
            path_sf.append(x[0])
            path_sf.append(dbp.get_label(x[1:]))
        true_path = entity_sf + path_sf

        """
            Create all possible paths.
            Then choose some of them.
        """
        false_paths = []

        # Collect 1st hop ones.
        for pospath in _raw[u'training'][entity][u'rel1'][0]:
            new_fp = entity_sf + ['+'] + nlutils.tokenize(dbp.get_label(pospath))
            false_paths.append(new_fp)

        for negpath in _raw[u'training'][entity][u'rel1'][1]:
            new_fp = entity_sf + ['-'] + nlutils.tokenize(dbp.get_label(negpath))
            false_paths.append(new_fp)

        # Collect 2nd hop ones
        try:

            # Access first element inside rel0 (for paths in direction + )
            for poshop1 in _raw[u'training'][entity][u'rel2'][0]:
                new_fp = entity_sf + ['+']

                hop1 = poshop1.keys()[0]
                hop1sf = dbp.get_label(hop1.replace(",", ""))
                new_fp += nlutils.tokenize(hop1sf)

                for poshop2 in poshop1[hop1][0]:
                    temp_fp = new_fp[:] + ['+'] + nlutils.tokenize(dbp.get_label(poshop2))
                    false_paths.append(temp_fp)

                for neghop2 in poshop1[hop1][1]:
                    temp_fp = new_fp[:] + ['-'] + nlutils.tokenize(dbp.get_label(neghop2))
                    false_paths.append(temp_fp)

            # Access second element inside rel0 (for paths in direction - )
            for neghop1 in _raw[u'training'][entity][u'rel2'][1]:
                new_fp = entity_sf + ['-']

                hop1 = neghop1.keys()[0]
                hop1sf = dbp.get_label(hop1.replace(",", ""))
                new_fp += nlutils.tokenize(hop1sf)

                for poshop2 in neghop1[hop1][0]:
                    temp_fp = new_fp[:] + ['+'] + nlutils.tokenize(dbp.get_label(poshop2))
                    false_paths.append(temp_fp)

                for neghop2 in neghop1[hop1][1]:
                    temp_fp = new_fp[:] + ['-'] + nlutils.tokenize(dbp.get_label(neghop2))
                    false_paths.append(temp_fp)

        except KeyError:

            # In case there isn't no rel2 in the subgraph, just go with 1 hop paths.
            pass

        except AttributeError:

            pprint(_raw)
            raw_input("Press Enter to continue")

        # From all these paths, randomly choose some.
        false_paths = np.random.choice(false_paths, MAX_FALSE_PATHS)

        """
            **rdf-type constraints:**

            !!NOTE!! Parser does not care which variable has the type constraint.

            Decisions:
                - For true path, everytime you find rdf type constraint, add to true paths
                - Add the original true path to false paths
                            @TODO @nilesh-c shall I remove s'thing from false paths, then?

                - For false paths:
                    - collect all false classes for both uri and x;
                    - for every false path (post random selection)
                        - randomly choose whether or not to add false class (p = 0.3)
                    - @TODO: @nilesh-c: shall we add true_path + incorrect_classes in false paths too?
        """
        true_class = None
        if '?uri' in _raw[u'constraints'].keys() or '?x' in _raw[u'constraints'].keys():
            # Question has type constraints

            if '?uri' in _raw[u'constraints'].keys():
                # Have a type constraint on the answer.
                true_class = _raw[u'constraints'][u'?uri']

            elif '?x' in _raw[u'constraints'].keys():
                # Have a type constraint on the intermediary variable.
                true_class = _raw[u'constraints'][u'?x']

            # Add the path (without type constraint) in false paths.
            false_paths = [true_path[:]] + false_paths.tolist()[1:]    # NOTE: Removing first false path.

            true_path += ['/']
            true_path += nlutils.tokenize(dbp.get_label(true_class), _ignore_brackets=False)

        try:
            if _raw[u'training'][u'uri'] or _raw[u'training'][u'x']:

                # Find all false classes
                f_classes = list(set(_raw[u'training'][u'uri'] + _raw[u'training'][u'x']))

                # Remove correct ones.
                if true_class in f_classes:
                    f_classes.remove(true_class)

                # Get surface form, tokenize.
                f_classes = [nlutils.tokenize(dbp.get_label(x), _ignore_brackets=False) for x in f_classes]

                for i in range(len(false_paths)):

                    # Stochastically decide if we want type restrictions there
                    if random.random() < pADDTYPE:

                        # If here, choose a random class, add to path
                        path = false_paths[i]
                        path += ['/']
                        path += random.choice(f_classes)

                        # Append path back to list
                        false_paths[i] = path

        except KeyError:

            if DEBUG:
                print("KeyError while parsing rdf:type (False). Datafile: ")
                pprint(_raw)

        # Vectorize paths
        id_true_path = embeddings_interface.vocabularize(true_path)
        id_false_paths = [embeddings_interface.vocabularize(x) for x in false_paths]

        # Corresponding to all these, compute the true labels
        v_y_true = compute_true_labels(question, true_path, false_paths)

        # Throw it out.
        return id_question, id_true_path, id_false_paths, v_y_true

    else:

        return -1

def run(_readfiledir='data/preprocesseddata_new_v2/', _writefilename='data/training/pairwise/',
        _phase_i_dir='resources/data_embedded_phase_i.pickle'):
    """
    Get the show on the road.

    :param _readfiledir:   the filename (directory info included) to read the JSONs that need parsing
    :param _writefilename: the file to which the parsed (embedded+padded) data is to be written to
    :param _phase_i_dir:   the filename (directory info included) where the script would read/write the intermediate \
                                pickles

    :return: zilch
    """

    try:

        # If the phase one is already done and then the code quit (errors/s'thing else), resume for efficiency's sake.
        data_embedded = pickle.load(open(_phase_i_dir))
        embedding_dim = data_embedded[0][0].shape[1]

        if DEBUG:
            print("Phase I State save found and loaded. Program will now end much faster.")

    except:

        # If here, we didn't resume the thing mid way but start afresh
        if DEBUG:
            warnings.warn("Phase I state save not found on disk. Go brew your coffee now.")

        '''
            Phase I - Embedding

            Read JSONs from every file.
            Parse every JSON (vectorized question, true and false paths)
            Collect the vectorized things in a variable.
        '''

        # Create vars to keep ze data @TODO: think of datatype here
        data_embedded = []

        # Pull all filenames from datafolder
        iterable = os.listdir(_readfiledir)

        # Shuffle Shuffle
        random.shuffle(iterable)

        if DEBUG:
            prog_bar = ProgressBar()
            iterable = prog_bar(iterable)
            print("parser: phase I: Started reading JSONs from disk.")

        # Read JSON files.
        for filename in iterable:
            data = json.load(open(os.path.join(_readfiledir, filename)))

            # Shuffle data too
            random.shuffle(data)

            # Each file has multiple datapoints (questions).
            for question in data:

                # Collect the response
                ops = parse(question)

                if ops == -1:
                    continue

                id_q, id_tp, id_fps, v_y = ops

                # Collect data for each question
                data_embedded.append([id_q, id_tp, id_fps, v_y])

        if DEBUG:
            print("""
                Phase I - Embedding DONE

            Read JSONs from every file.
            Parse every JSON (vectorized question, true and false paths)
            Collect the vectorized things in a variable.
            """)

        f = open(_phase_i_dir, 'w+')
        pickle.dump(data_embedded, f)
        f.close()


def test():
    """
        A function to test different things in the script. Will be called from main.

    :return: noting
    """

    """
        Embedding Tests:
            1. Load an embedding
            2. Check it for some sentences
            3. Fire up a loop for token tests
            4. Run it on LC QuAD and report the number of questions with unks and total unks encountered.
    """
    options = raw_input("Type in the numbers corresponding to the tests you want to run.")

    if "1" in options:

        print "Finished loading the embedding. Moving on."

    if "2" in options:
        sents = [
            "Who is the president of United States?",
            "For which band does Joe Hahn perform?",
            "Name some people playing for the Turkish handball league?"
        ]

        for sentence in sents:
            embedding = embeddings_interface.vectorize(nlutils.tokenize(sentence))
            pprint(embedding)
            raw_input(sentence)

    if "3" in options:
        while True:
            tok = raw_input("Enter your word")
            if tok.lower() in ["stop", "bye", "q", "quit", "exit"]: break
            tok = tok.strip()

            # Quickly manage the multi word problem
            if ' ' in tok:
                tok = tok.split()
            else:
                tok = [tok]

            result = embeddings_interface.vectorize(tok)
            print result
            try:
                print result.shape
            except AttributeError:
                traceback.print_exc()

    if "4" in options:
        # @TODO: implement this.
        pass

    """
        Parsing tests.
    """
    # Load any JSON
    testdata = json.load(open('data/preprocesseddata_new_v2/1294.json'))[4]
    op = parse(testdata)
    print op


    # testdata = json.load(open(os.path.join('data', os.listdir('data')[0])))[-2]
    # # data = data.replace("'", '"')
    # q, tp, fp, y = parse(testdata)
    # pprint(q)
    # pprint(tp)
    # pprint(fp)
    # pprint(y)


if __name__ == "__main__":
    run()
