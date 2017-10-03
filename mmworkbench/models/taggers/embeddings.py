import os
import zipfile
import logging
import pickle
import numpy as np

from tqdm import tqdm
from six.moves.urllib.request import urlretrieve

from ...path import EMBEDDINGS_FILE_PATH, \
    EMBEDDINGS_FOLDER_PATH, PREVIOUSLY_USED_WORD_EMBEDDINGS_FILE_PATH, \
    PREVIOUSLY_USED_CHAR_EMBEDDINGS_FILE_PATH
from ...exceptions import EmbeddingDownloadError

logger = logging.getLogger(__name__)

GLOVE_DOWNLOAD_LINK = 'http://nlp.stanford.edu/data/glove.6B.zip'
EMBEDDING_FILE_PATH_TEMPLATE = 'glove.6B.{}d.txt'
ALLOWED_WORD_EMBEDDING_DIMENSIONS = [50, 100, 200, 300]


class TqdmUpTo(tqdm):
    """Provides `update_to(n)` which uses `tqdm.update(delta_n)`."""

    def update_to(self, b=1, bsize=1, tsize=None):
        """Reports update statistics on the download progress

        Args:
            b (int): Number of blocks transferred so far [default: 1]
            bsize (int): Size of each block (in tqdm units) [default: 1]
            tsize (int): Total size (in tqdm units). If [default: None] remains unchanged.
        """
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)  # will also set self.n = b * bsize


class GloVeEmbeddingsContainer:
    """This class is responsible for the downloading, extraction and storing of
    word embeddings based on the GloVe format"""

    def __init__(self, token_dimension=300, token_pretrained_embedding_filepath=None):

        self.token_pretrained_embedding_filepath = \
            token_pretrained_embedding_filepath

        self.token_dimension = token_dimension

        if self.token_dimension not in ALLOWED_WORD_EMBEDDING_DIMENSIONS:
            logger.info("Token dimension {} not supported, "
                        "chose from these dimensions: {}. "
                        "Selected 300 by default".format(token_dimension,
                                                         str(ALLOWED_WORD_EMBEDDING_DIMENSIONS)))
            self.token_dimension = 300

        self.word_to_embedding = {}
        self._extract_embeddings()

    def get_pretrained_word_to_embeddings_dict(self):
        """Returns the word to embedding dict

        Returns:
            (dict): word to embedding mapping
        """
        return self.word_to_embedding

    def _download_embeddings_and_return_zip_handle(self):

        logger.info("Downloading embedding from {}".format(GLOVE_DOWNLOAD_LINK))

        # Make the folder that will contain the embeddings
        if not os.path.exists(EMBEDDINGS_FOLDER_PATH):
            os.makedirs(EMBEDDINGS_FOLDER_PATH)

        with TqdmUpTo(unit='B', unit_scale=True, miniters=1,
                      desc=GLOVE_DOWNLOAD_LINK) as t:

            try:
                urlretrieve(GLOVE_DOWNLOAD_LINK, EMBEDDINGS_FILE_PATH, reporthook=t.update_to)

            except Exception as e:
                logger.error("There was an issue downloading from this "
                             "link {} with the following error: "
                             "{}".format(GLOVE_DOWNLOAD_LINK, e))
                return

            file_name = EMBEDDING_FILE_PATH_TEMPLATE.format(self.token_dimension)
            zip_file_object = zipfile.ZipFile(EMBEDDINGS_FILE_PATH, 'r')

            if file_name not in zip_file_object.namelist():
                logger.info("Embedding file with {} dimensions "
                            "not found".format(self.token_dimension))
                return

            return zip_file_object

    def _extract_and_map(self, glove_file):
        for line in glove_file:
            values = line.split()
            word = values[0]
            coefs = np.asarray(values[1:], dtype='float32')
            self.word_to_embedding[word] = coefs

    def _extract_embeddings(self):
        file_location = self.token_pretrained_embedding_filepath

        if file_location and os.path.isfile(file_location):
            logger.info("Extracting embeddings from provided "
                        "file location {}".format(str(file_location)))
            with open(file_location, 'r') as embedding_file:
                self._extract_and_map(embedding_file)
            return

        logger.info("Provided file location {} does not exist".format(str(file_location)))

        file_name = EMBEDDING_FILE_PATH_TEMPLATE.format(self.token_dimension)

        if os.path.isfile(EMBEDDINGS_FILE_PATH):
            logger.info("Extracting embeddings from default folder "
                        "location {}".format(EMBEDDINGS_FILE_PATH))

            try:
                zip_file_object = zipfile.ZipFile(EMBEDDINGS_FILE_PATH, 'r')
                with zip_file_object.open(file_name) as embedding_file:
                    self._extract_and_map(embedding_file)
            except zipfile.BadZipFile:
                logger.warning("{} is corrupt. Deleting the zip file and attempting to"
                               " download the embedding file again".format(EMBEDDINGS_FILE_PATH))
                os.remove(EMBEDDINGS_FILE_PATH)
                self._extract_embeddings()
            except:
                logger.error("An error occurred when reading {} zip file. The file might"
                             " be corrupt, so try deleting the file and running the program "
                             "again".format(EMBEDDINGS_FILE_PATH))
            return

        logger.info("Default folder location {} does not exist".format(EMBEDDINGS_FILE_PATH))

        zip_file_object = self._download_embeddings_and_return_zip_handle()

        if not zip_file_object:
            raise EmbeddingDownloadError("Failed to download embeddings")

        with zip_file_object.open(file_name) as embedding_file:
            self._extract_and_map(embedding_file)
        return


class WordSequenceEmbedding(object):

    def __init__(self,
                 sequence_padding_length,
                 default_token,
                 token_embedding_dimension=None,
                 token_pretrained_embedding_filepath=None):
        """Initializes the WordSequenceEmbedding class

        Args:
            sequence_padding_length (int): padding length of the sequence after which
            the sequence is cut off
            default_token (str): The default token if the sequence is too short for
            the fixed padding length
            token_embedding_dimension (int): The embedding dimension of the token
            token_pretrained_embedding_filepath (str): The embedding filepath to
            extract the embeddings from.
        """
        self.token_embedding_dimension = token_embedding_dimension
        self.sequence_padding_length = sequence_padding_length

        self.token_to_embedding_mapping = {}

        self.token_to_embedding_mapping = GloVeEmbeddingsContainer(
            token_embedding_dimension,
            token_pretrained_embedding_filepath).get_pretrained_word_to_embeddings_dict()

        self.default_token = default_token
        self._add_historic_embeddings()

    def encode_sequence_of_tokens(self, token_sequence):
        """Encodes a sequence of tokens

        Args:
            token_sequence (list): A sequence of tokens

        Returns:
            (list): Encoded sequence of tokens
        """
        default_encoding = np.zeros(self.token_embedding_dimension)
        self.token_to_embedding_mapping[self.default_token] = default_encoding
        encoded_query = [default_encoding] * self.sequence_padding_length

        for idx, token in enumerate(token_sequence):
            if idx >= self.sequence_padding_length:
                break
            encoded_query[idx] = self._encode_token(token)

        return encoded_query

    def _encode_token(self, token):
        """Encodes a token to it's corresponding embedding

        Args:
            token (str): Individual token

        Returns:
            corresponding embedding
        """
        if token not in self.token_to_embedding_mapping:
            random_vector = np.random.uniform(-1, 1, size=(self.token_embedding_dimension,))
            self.token_to_embedding_mapping[token] = random_vector
        return self.token_to_embedding_mapping[token]

    def _add_historic_embeddings(self):
        historic_word_embeddings = {}

        # load historic word embeddings
        if os.path.exists(PREVIOUSLY_USED_WORD_EMBEDDINGS_FILE_PATH):
            pkl_file = open(PREVIOUSLY_USED_WORD_EMBEDDINGS_FILE_PATH, 'rb')
            historic_word_embeddings = pickle.load(pkl_file)
            pkl_file.close()

        for word in historic_word_embeddings:
            self.token_to_embedding_mapping[word] = historic_word_embeddings.get(word)

        # save extracted embeddings to historic pickle file
        output = open(PREVIOUSLY_USED_WORD_EMBEDDINGS_FILE_PATH, 'wb')
        pickle.dump(self.token_to_embedding_mapping, output)
        output.close()


class CharacterSequenceEmbedding(object):

    def __init__(self,
                 sequence_padding_length,
                 default_token,
                 token_embedding_dimension=None,
                 max_char_per_word=None):
        """Initializes the CharacterSequenceEmbedding class

        Args:
            sequence_padding_length (int): padding length of the sequence after which
            the sequence is cut off
            default_token (str): The default token if the sequence is too short for
            the fixed padding length
            token_embedding_dimension (int): The embedding dimension of the token
            max_char_per_word (int): The maximum number of characters per word
        """
        self.token_embedding_dimension = token_embedding_dimension
        self.sequence_padding_length = sequence_padding_length
        self.max_char_per_word = max_char_per_word
        self.token_to_embedding_mapping = {}
        self.default_token = default_token
        self._add_historic_embeddings()

    def encode_sequence_of_tokens(self, token_sequence):
        """Encodes a sequence of tokens

        Args:
            token_sequence (list): A sequence of tokens

        Returns:
            (list): Encoded sequence of tokens
        """
        default_encoding = np.zeros(self.token_embedding_dimension)
        self.token_to_embedding_mapping[self.default_token] = default_encoding
        default_char_word = [default_encoding] * self.max_char_per_word
        encoded_query = [default_char_word] * self.sequence_padding_length

        for idx, word_token in enumerate(token_sequence):
            if idx >= self.sequence_padding_length:
                break

            encoded_word = \
                [self.token_to_embedding_mapping[self.default_token]] * self.max_char_per_word

            for idx2, char_token in enumerate(word_token):
                if idx2 >= self.max_char_per_word:
                    break

                self._encode_token(char_token)
                encoded_word[idx2] = self.token_to_embedding_mapping[char_token]

            encoded_query[idx] = encoded_word
        return encoded_query

    def _encode_token(self, token):
        """Encodes a token to it's corresponding embedding

        Args:
            token (str): Individual token

        Returns:
            corresponding embedding
        """
        if token not in self.token_to_embedding_mapping:
            random_vector = np.random.uniform(-1, 1, size=(self.token_embedding_dimension,))
            self.token_to_embedding_mapping[token] = random_vector
        return self.token_to_embedding_mapping[token]

    def _add_historic_embeddings(self):
        historic_char_embeddings = {}

        # load historic word embeddings
        if os.path.exists(PREVIOUSLY_USED_CHAR_EMBEDDINGS_FILE_PATH):
            pkl_file = open(PREVIOUSLY_USED_CHAR_EMBEDDINGS_FILE_PATH, 'rb')
            historic_char_embeddings = pickle.load(pkl_file)
            pkl_file.close()

        for char in historic_char_embeddings:
            self.token_to_embedding_mapping[char] = historic_char_embeddings.get(char)

        # save extracted embeddings to historic pickle file
        output = open(PREVIOUSLY_USED_CHAR_EMBEDDINGS_FILE_PATH, 'wb')
        pickle.dump(self.token_to_embedding_mapping, output)
        output.close()
