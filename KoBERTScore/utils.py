import math
import numpy as np
from collections import Counter
from scipy.stats import pearsonr
from tqdm import tqdm

from .score import sents_to_tensor, bert_forwarding, compute_RPF


def train_idf(bert_tokenizer, references, batch_size=1000, verbose=True):
    """
    Train IDF vector with Laplace (add one) smoothing

    Args:
        bert_tokenizer (transformers.PreTrainedTokenizer)
        references (list of str) : True sentences
        batch_size (int)
        verbose (Boolean)

    Returns:
        idf (numpy.ndarray) : shape = (bert_tokenizer.vocab_size,)
    """
    n_sents = len(references)
    counter = Counter()
    begin_index = list(range(0, n_sents, batch_size))

    if verbose:
        iterator = tqdm(begin_index, total=round(n_sents / batch_size), desc='Train IDF')
    else:
        iterator = begin_index

    for i in iterator:
        encoding = tokenizer.batch_encode_plus(
            references[i: i + batch_size],
            add_special_tokens=False)
        subcounter = Counter(idx for sent in encoding['input_ids'] for idx in sent)
        counter.update(subcounter)

    idf = np.ones(tokenizer.vocab_size)
    indices, df = zip(*counter.items())
    idf[np.array(indices)] += np.array(df)
    idf = 1 / idf
    return idf


def correlation(bert_tokenizer, bert_model, references, candidates, qualities,
                idf=None, rescale_base=0, batch_size=128):
    """
    Args:
        bert_tokenizer (transformers.PreTrainedTokenizer)
        bert_model (transformers`s Pretrained models)
        references (list of str) : True sentences
        candidates (list of str) : Generated sentences
        qualities (list of float) : True qualities between (reference, candidate)
        idf (torch.nn.Embedding or None) : IDF weights
        rescale_base (float) : 0 <= rescale_base < 1
            Adjust (R-BERTScore - base) / (1 - base)
        batch_size (int) : Batch size, default = 128

    Returns:
        R (dict) : {layer: correlation}
        P (dict) : {layer: correlation}
        F (dict) : {layer: correlation}

    Examples::
        >>> from transformers import BertModel, BertTokenizer

        >>> model_name = "beomi/kcbert-base"
        >>> tokenizer = BertTokenizer.from_pretrained(model_name)
        >>> encoder = BertModel.from_pretrained(model_name)

        >>> references = ['날씨는 좋고 할일은 많고 어우 연휴 끝났다', '힘을 내볼까? 잘할 수 있어!', '이 문장은 점수가 낮아야만 합니다']
        >>> candidates = ['날씨가 좋다 하지만 할일이 많다 일해라 인간', '힘내라 잘할 수 있다', '테넷봤나요? 역의역의역은역인가요?']
        >>> qualities = [0.85, 0.98, 0.05]

        >>> R, P, F = correlation(tokenizer, encoder, references, candidates, qualities)
        >>> R
        $ {0: 0.9999654999597412,
           1: 0.9992112037241504,
           2: 0.9965136571004495,
           3: 0.9957015840472935,
           4: 0.9988308396315225,
           5: 0.996627590921058,
           6: 0.9945366957299662,
           7: 0.993955314845382,
           8: 0.9934660109682587,
           9: 0.9937264961902929,
           10: 0.9953018679381236,
           11: 0.9985711230470845,
           12: 0.9992405789378926}
    """
    if not isinstance(qualities, np.ndarray):
        qualities = np.array(qualities)

    # Initialize
    n_layers = bert_model.config.num_hidden_layers + 1
    R, P, F = {}, {}, {}
    for layer in range(n_layers):
        R[layer] = []
        P[layer] = []
        F[layer] = []

    n_examples = len(references)
    n_batch = math.ceil(n_examples / batch_size)
    for step in tqdm(range(n_batch), desc='Calculating R, P, F', total=n_batch):
        b = step * batch_size
        e = min((step + 1) * batch_size, n_examples)
        refer_batch = references[b: e]
        candi_batch = candidates[b: e]
        qual_batch = qualities[b: e]

        refer_ids, refer_attention_mask, refer_weight_mask = sents_to_tensor(bert_tokenizer, refer_batch)
        candi_ids, candi_attention_mask, candi_weight_mask = sents_to_tensor(bert_tokenizer, candi_batch)

        refer_embeds = bert_forwarding(bert_model, refer_ids, refer_attention_mask, output_layer_index='all')
        candi_embeds = bert_forwarding(bert_model, candi_ids, candi_attention_mask, output_layer_index='all')

        for layer in range(n_layers):
            refer_embeds_i = refer_embeds[layer]
            candi_embeds_i = candi_embeds[layer]
            R_l, P_l, F_l = compute_RPF(
                refer_embeds_i, candi_embeds_i,
                refer_weight_mask, candi_weight_mask,
                refer_ids, candi_ids,
                idf, rescale_base
            )
            R[layer].append(R_l.numpy())
            P[layer].append(P_l.numpy())
            F[layer].append(F_l.numpy())

    def corr(array):
        return pearsonr(qualities, array)[0]

    R = {layer: corr(np.concatenate(array)) for layer, array in R.items()}
    P = {layer: corr(np.concatenate(array)) for layer, array in P.items()}
    F = {layer: corr(np.concatenate(array)) for layer, array in F.items()}
    return R, P, F
