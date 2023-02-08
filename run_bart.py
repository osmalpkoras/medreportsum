# coding=utf-8

# the following codes are based on huggingface/transformers
# https://github.com/huggingface/transformers/blob/master/examples/pytorch/summarization/run_summarization.py

import os
os.environ['WANDB_DISABLED'] = 'true'

import sys
import logging
# from rouge.rouge_score import Ngrams
import torch
from torch.autograd.grad_mode import F
import transformers
import nltk
import numpy as np
# import time
import json
from dataclasses import dataclass, field
import datasets
from datasets import load_dataset, load_metric
from typing import Optional, List
# from transformers.file_utils import is_offline_mode
from transformers.trainer_utils import get_last_checkpoint
# from transformers.utils import check_min_version
# from transformers.utils.versions import require_version
from transformers import (
    AutoConfig, AutoTokenizer,
    DataCollatorForSeq2Seq, HfArgumentParser, set_seed
)
# from transformers.deepspeed import is_deepspeed_zero3_enabled

from transformers.trainer_callback import EarlyStoppingCallback
from transformers.training_args import TrainingArguments
# from extoracle.utils import greedy_selection
# from nltk.tokenize import sent_tokenize, word_tokenize
# from rouge_score import rouge_scorer, scoring

sys.path.append(".")
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

from prettytable import PrettyTable

# from transformers.models.bart.modeling_bart import BartForConditionalGeneration
# from transformers import Seq2SeqTrainer

from trainer_seq2seq import Seq2SeqTrainer
from modeling_bart import BartForConditionalGeneration

from nltk.tokenize import sent_tokenize

import nltk
nltk.download('punkt')

logger = logging.getLogger(__name__)

def count_parameters(model, all_param=False):
    table = PrettyTable(["Modules", "Parameters"])
    total_params = 0
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad: 
            continue
        param = parameter.numel()
        table.add_row([name, param])
        total_params+=param
    if all_param:
        logger.warning(table)
    logger.warning(f"Total Trainable Params: {total_params}")
    return total_params


@dataclass
class Seq2SeqTrainingArguments(TrainingArguments):
    """
    sortish_sampler (:obj:`bool`, `optional`, defaults to :obj:`False`):
        Whether to use a `sortish sampler` or not. Only possible if the underlying datasets are `Seq2SeqDataset` for
        now but will become generally available in the near future.

        It sorts the inputs according to lengths in order to minimize the padding size, with a bit of randomness for
        the training set.
    predict_with_generate (:obj:`bool`, `optional`, defaults to :obj:`False`):
        Whether to use generate to calculate generative metrics (ROUGE, BLEU).
    generation_max_length (:obj:`int`, `optional`):
        The :obj:`max_length` to use on each evaluation loop when :obj:`predict_with_generate=True`. Will default to
        the :obj:`max_length` value of the model configuration.
    generation_num_beams (:obj:`int`, `optional`):
        The :obj:`num_beams` to use on each evaluation loop when :obj:`predict_with_generate=True`. Will default to the
        :obj:`num_beams` value of the model configuration.
    """

    sortish_sampler: bool = field(default=False, metadata={"help": "Whether to use SortishSampler or not."})
    predict_with_generate: bool = field(
        default=False, metadata={"help": "Whether to use generate to calculate generative metrics (ROUGE, BLEU)."}
    )
    generation_max_length: Optional[int] = field(
        default=None,
        metadata={
            "help": "The `max_length` to use on each evaluation loop when `predict_with_generate=True`. Will default "
            "to the `max_length` value of the model configuration."
        },
    )
    generation_min_length: Optional[int] = field(
        default=None,
    )
    generation_num_beams: Optional[int] = field(
        default=None,
        metadata={
            "help": "The `num_beams` to use on each evaluation loop when `predict_with_generate=True`. Will default "
            "to the `num_beams` value of the model configuration."
        },
    )
    do_sample: Optional[bool] = field(
        default=False,
        metadata={
            "help": "Whether use do_sample"
        }
    )
    top_p: Optional[float] = field(
        default=None,
        metadata={
            "help": "top-p nucleus sampling"
        }
    )
    temperature: Optional[float] = field(
        default=None,
        metadata={
            "help": "temperature"
        }
    )
    evaltest_generation_max_length: Optional[int] = field(
        default=None,
    )
    evaltest_generation_num_beams: Optional[int] = field(
        default=None,
    )

@dataclass
class ModelArguments:
    """
    Arguments pertaining to which model/config/tokenizer we are going to fine-tune from.
    """
    model_name_or_path: str = field(
        metadata={"help": "Path to pretrained model or model identifier from huggingface.co/models"}
    )
    config_name: Optional[str] = field(
        default=None, metadata={"help": "Pretrained config name or path if not the same as model_name"}
    )
    tokenizer_name: Optional[str] = field(
        default=None, metadata={"help": "Pretrained tokenizer name or path if not the same as model_name"}
    )
    cache_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Where to store the pretrained models downloaded from huggingface.co"},
    )
    use_fast_tokenizer: bool = field(
        default=True,
        metadata={"help": "Whether to use one of the fast tokenizer (backed by the tokenizers library) or not."},
    )
    model_revision: str = field(
        default="main",
        metadata={"help": "The specific model version to use (can be a branch name, tag name or commit id)."},
    )
    use_auth_token: bool = field(
        default=False,
        metadata={
            "help": "Will use the token generated when running `transformers-cli login` (necessary to use this script "
            "with private models)."
        },
    )
    resize_position_embeddings: Optional[bool] = field(
        default=None,
        metadata={
            "help": "Whether to automatically resize the position embeddings if `max_source_length` exceeds "
            "the model's position embeddings."
        },
    )

@dataclass
class DataTrainingArguments:
    """
    Arguments pertaining to what data we are going to input our model for training and eval.
    """

    dataset_name: Optional[str] = field(
        default=None, metadata={"help": "The name of the dataset to use (via the datasets library)."}
    )
    dataset_config_name: Optional[str] = field(
        default=None, metadata={"help": "The configuration name of the dataset to use (via the datasets library)."}
    )
    text_column: Optional[str] = field(
        default=None,
        metadata={"help": "The name of the column in the datasets containing the full texts (for summarization)."},
    )
    summary_column: Optional[str] = field(
        default=None,
        metadata={"help": "The name of the column in the datasets containing the summaries (for summarization)."},
    )
    train_file: Optional[str] = field(
        default=None, metadata={"help": "The input training data file (a jsonlines or csv file)."}
    )
    validation_file: Optional[str] = field(
        default=None,
        metadata={
            "help": "An optional input evaluation data file to evaluate the metrics (rouge) on "
            "(a jsonlines or csv file)."
        },
    )
    test_file: Optional[str] = field(
        default=None,
        metadata={
            "help": "An optional input test data file to evaluate the metrics (rouge) on " "(a jsonlines or csv file)."
        },
    )
    overwrite_cache: bool = field(
        default=False, metadata={"help": "Overwrite the cached training and evaluation sets"}
    )
    preprocessing_num_workers: Optional[int] = field(
        default=None,
        metadata={"help": "The number of processes to use for the preprocessing."},
    )
    max_source_length: Optional[int] = field(
        default=1024,
        metadata={
            "help": "The maximum total input sequence length after tokenization. Sequences longer "
            "than this will be truncated, sequences shorter will be padded."
        },
    )
    max_target_length: Optional[int] = field(
        default=128,
        metadata={
            "help": "The maximum total sequence length for target text after tokenization. Sequences longer "
            "than this will be truncated, sequences shorter will be padded."
        },
    )
    val_max_target_length: Optional[int] = field(
        default=None,
        metadata={
            "help": "The maximum total sequence length for validation target text after tokenization. Sequences longer "
            "than this will be truncated, sequences shorter will be padded. Will default to `max_target_length`."
            "This argument is also used to override the ``max_length`` param of ``model.generate``, which is used "
            "during ``evaluate`` and ``predict``."
        },
    )
    pad_to_max_length: bool = field(
        default=False,
        metadata={
            "help": "Whether to pad all samples to model maximum sentence length. "
            "If False, will pad the samples dynamically when batching to the maximum length in the batch. More "
            "efficient on GPU but very bad for TPU."
        },
    )
    max_train_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of training examples to this "
            "value if set."
        },
    )
    max_eval_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of evaluation examples to this "
            "value if set."
        },
    )
    max_predict_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "For debugging purposes or quicker training, truncate the number of prediction examples to this "
            "value if set."
        },
    )
    num_beams: Optional[int] = field(
        default=None,
        metadata={
            "help": "Number of beams to use for evaluation. This argument will be passed to ``model.generate``, "
            "which is used during ``evaluate`` and ``predict``."
        },
    )
    ignore_pad_token_for_loss: bool = field(
        default=True,
        metadata={
            "help": "Whether to ignore the tokens corresponding to padded labels in the loss computation or not."
        },
    )
    source_prefix: Optional[str] = field(
        default=None, metadata={"help": "A prefix to add before every source text (useful for T5 models)."}
    )
    use_sampleprompt: Optional[bool] = field(
        default=False
    )
    sampleprompt: Optional[str] = field(
        default=None
    )

    def __post_init__(self):
        if self.dataset_name is None and self.train_file is None and self.validation_file is None:
            raise ValueError("Need either a dataset name or a training/validation file.")
        else:
            if self.train_file is not None:
                extension = self.train_file.split(".")[-1]
                assert extension in ["csv", "json"], "`train_file` should be a csv or a json file."
            if self.validation_file is not None:
                extension = self.validation_file.split(".")[-1]
                assert extension in ["csv", "json"], "`validation_file` should be a csv or a json file."
        if self.val_max_target_length is None:
            self.val_max_target_length = self.max_target_length

summarization_name_mapping = {
    "amazon_reviews_multi": ("review_body", "review_title"),
    "big_patent": ("description", "abstract"),
    "cnn_dailymail": ("article", "highlights"),
    "orange_sum": ("text", "summary"),
    "pn_summary": ("article", "summary"),
    "psc": ("extract_text", "summary_text"),
    "samsum": ("dialogue", "summary"),
    "thaisum": ("body", "summary"),
    "xglue": ("news_body", "news_title"),
    "xsum": ("document", "summary"),
    "wiki_summary": ("article", "highlights"),
}

def run_bart():
    logger.info('torch version:', torch.__version__, 'cuda available:',torch.cuda.is_available())
    torch_device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if torch_device == 'cuda':
        logger.info('device count:', torch.cuda.device_count(), torch_device)
        logger.info('cuda device:', torch.cuda.get_device_name(0))

    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, Seq2SeqTrainingArguments))
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        model_args, data_args, training_args = parser.parse_json_file(json_file=os.path.abspath(sys.argv[1]))
    else:
        model_args, data_args, training_args = parser.parse_args_into_dataclasses()

    training_args.sampleprompt = data_args.sampleprompt
    print(f'sampleprompt {data_args.sampleprompt}')
    
    if data_args.sampleprompt == "sampleprom4":
        json_name = "DISCHARGE"
        if "ECHO_" in data_args.dataset_name:
            json_name = "ECHO"
        elif "RADIOLOGY_" in data_args.dataset_name:
            json_name = "RADIOLOGY"
        
        with open("./dataset/" + json_name + "_cluster.json", "r", encoding="utf-8") as read_file:
            cluster_classes = json.load(read_file)
            print(f"json {json_name} _cluster.json loaded, key size = {len(cluster_classes.keys())}")
            training_args.cluster_classes = cluster_classes

    # Setup logging
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log_level = training_args.get_process_log_level()

    dataset_loglevel = 20
    transformers_loglevel = 30

    logger.setLevel(log_level)
    datasets.utils.logging.set_verbosity(dataset_loglevel)
    transformers.utils.logging.set_verbosity(transformers_loglevel)
    transformers.utils.logging.enable_default_handler()
    transformers.utils.logging.enable_explicit_format()

    # Log on each process the small summary:
    logger.warning(
        f"Process rank: {training_args.local_rank}, device: {training_args.device}, n_gpu: {training_args.n_gpu}"
        + f"distributed training: {bool(training_args.local_rank != -1)}, 16-bits training: {training_args.fp16}"
    )
    logger.info(f"Training/evaluation parameters {training_args}")

    if data_args.source_prefix is None and model_args.model_name_or_path in [
        "t5-small",
        "t5-base",
        "t5-large",
        "t5-3b",
        "t5-11b",
    ]:
        logger.warning(
            "You're running a t5 model but didn't provide a source prefix, which is the expected, e.g. with "
            "`--source_prefix 'summarize: ' `"
        )

    # Detecting last checkpoint.
    last_checkpoint = None
    if os.path.isdir(training_args.output_dir) and training_args.do_train and not training_args.overwrite_output_dir:
        last_checkpoint = get_last_checkpoint(training_args.output_dir)
        if last_checkpoint is None and len(os.listdir(training_args.output_dir)) > 0:
            raise ValueError(
                f"Output directory ({training_args.output_dir}) already exists and is not empty. "
                "Use --overwrite_output_dir to overcome."
            )
        elif last_checkpoint is not None and training_args.resume_from_checkpoint is None:
            logger.info(
                f"Checkpoint detected, resuming training at {last_checkpoint}. To avoid this behavior, change "
                "the `--output_dir` or add `--overwrite_output_dir` to train from scratch."
            )

    set_seed(training_args.seed)
    
    # if data_args.dataset_name is not None:
    #     raw_datasets = load_dataset(
    #         data_args.dataset_name, 
    #         data_args.dataset_config_name, 
    #         cache_dir=model_args.cache_dir
    #     )
    # else:
    #     data_files = {}
    #     if data_args.train_file is not None:
    #         data_files["train"] = data_args.train_file
    #         extension = data_args.train_file.split(".")[-1]
    #     if data_args.validation_file is not None:
    #         data_files["validation"] = data_args.validation_file
    #         extension = data_args.validation_file.split(".")[-1]
    #     if data_args.test_file is not None:
    #         data_files["test"] = data_args.test_file
    #         extension = data_args.test_file.split(".")[-1]
    #     raw_datasets = load_dataset(extension, data_files=data_files, cache_dir=model_args.cache_dir)

    config = AutoConfig.from_pretrained(
        model_args.config_name if model_args.config_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
    )

    tokenizer = AutoTokenizer.from_pretrained(
        model_args.tokenizer_name if model_args.tokenizer_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        use_fast=model_args.use_fast_tokenizer,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
    )

    prefix = data_args.source_prefix if data_args.source_prefix is not None else ""
    # Preprocessing the datasets.
    # We need to tokenize inputs and targets.
    # if training_args.do_train:
    #     column_names = raw_datasets["train"].column_names
    # elif training_args.do_eval:
    #     column_names = raw_datasets["validation"].column_names
    # elif training_args.do_predict:
    #     column_names = raw_datasets["test"].column_names
    # else:
    #     logger.info("There is nothing to do. Please pass `do_train`, `do_eval` and/or `do_predict`.")
    #     return

    # Get the column names for input/target.
    # dataset_columns = summarization_name_mapping.get(data_args.dataset_name, None)
    dataset_columns = ['source', 'summary']
    column_names = ['source', 'summary']

    if data_args.text_column is None:
        text_column = dataset_columns[0] if dataset_columns is not None else column_names[0]
    else:
        text_column = data_args.text_column
        if text_column not in column_names:
            raise ValueError(
                f"--text_column' value '{data_args.text_column}' needs to be one of: {', '.join(column_names)}"
            )
    if data_args.summary_column is None:
        summary_column = dataset_columns[1] if dataset_columns is not None else column_names[1]
    else:
        summary_column = data_args.summary_column
        if summary_column not in column_names:
            raise ValueError(
                f"--summary_column' value '{data_args.summary_column}' needs to be one of: {', '.join(column_names)}"
            )
    # Temporarily set max_target_length for training.
    max_target_length = data_args.max_target_length
    padding = "max_length" if data_args.pad_to_max_length else False

    def preprocess_function(
            examples, 
            eval_or_predict=False,
            use_sampleprompt=False):

        pad_token_id = tokenizer.pad_token_id
        inputs = examples[text_column]
        targets = examples[summary_column]
        inputs = [prefix + inp for inp in inputs]
        model_inputs = tokenizer(inputs, max_length=data_args.max_source_length, padding=padding, truncation=True)
        labels = None

        if use_sampleprompt:
            sampleprompt = examples[data_args.sampleprompt]
            sampleprompt = tokenizer(sampleprompt, max_length=max_target_length, padding=padding, truncation=True)
            sampleprompt = sampleprompt["input_ids"]
            padded_prompt = []
            for encoded in sampleprompt:
                padded_encoded = list(encoded)
                
                for _ in range(max_target_length - len(encoded)):
                    padded_encoded.append(pad_token_id)
                padded_prompt.append(padded_encoded)
            
            model_inputs["sampleprompt"] = padded_prompt

        # Setup the tokenizer for targets
        with tokenizer.as_target_tokenizer():
            labels = tokenizer(targets, max_length=max_target_length, padding=padding, truncation=True)

        # If we are padding here, replace all tokenizer.pad_token_id in the labels by -100 when we want to ignore
        # padding in the loss.
        if padding == "max_length" and data_args.ignore_pad_token_for_loss:
            labels["input_ids"] = [
                [(l if l != tokenizer.pad_token_id else -100) for l in label] for label in labels["input_ids"]
            ]

        model_inputs["labels"] = labels["input_ids"]

        assert len(model_inputs["input_ids"]) == len(model_inputs["labels"]), f'len of model_inputs should == len labels, but is {len(model_inputs["input_ids"])} and {len(model_inputs["labels"])}'

        return model_inputs

    if training_args.do_train:
        # if "train" not in raw_datasets:
        #     raise ValueError("--do_train requires a train dataset")
        # train_dataset = raw_datasets["train"]

        train_dataset = load_dataset('json', data_files=data_args.dataset_name, field='train')
        train_dataset = train_dataset['train']

        # if data_args.max_train_samples is not None:
            # train_dataset = train_dataset.select(range(data_args.max_train_samples))
        
        with training_args.main_process_first(desc="train dataset map pre-processing"):
            train_dataset = train_dataset.map(
                preprocess_function,
                batched=True,
                num_proc=data_args.preprocessing_num_workers,
                remove_columns=column_names,
                load_from_cache_file=not data_args.overwrite_cache,
                desc="Running tokenizer on train dataset",
                fn_kwargs={
                            'use_sampleprompt': data_args.use_sampleprompt,
                }
            )

    if training_args.do_eval:
        max_target_length = data_args.val_max_target_length
        # if "validation" not in raw_datasets:
        #     raise ValueError("--do_eval requires a validation dataset")
        # eval_dataset = raw_datasets["validation"]
        
        eval_dataset = None
        if 'RADIOLOGY' in data_args.dataset_name:
            # eval set too large, therefore cutting off. 
            # test set remains same size.
            eval_dataset = load_dataset('json', 
                                        data_files=data_args.dataset_name, 
                                        field='eval', 
                                        split='train[:30%]')
        else:
            eval_dataset = load_dataset('json', data_files=data_args.dataset_name, field='eval')
            eval_dataset = eval_dataset['train']

        # if data_args.max_eval_samples is not None:
            # eval_dataset = eval_dataset.select(range(data_args.max_eval_samples))

        with training_args.main_process_first(desc="validation dataset map pre-processing"):
            eval_dataset = eval_dataset.map(
                preprocess_function,
                batched=True,
                num_proc=data_args.preprocessing_num_workers,
                remove_columns=column_names,
                load_from_cache_file=not data_args.overwrite_cache,
                desc="Running tokenizer on validation dataset",
                fn_kwargs={
                            'eval_or_predict': True,
                            'use_sampleprompt': data_args.use_sampleprompt,}
            )

    if training_args.do_predict:
        max_target_length = data_args.val_max_target_length
        # if "test" not in raw_datasets:
            # raise ValueError("--do_predict requires a test dataset")
        # predict_dataset = raw_datasets["test"]

        predict_dataset = load_dataset('json', data_files=data_args.dataset_name, field='test')
        predict_dataset = predict_dataset['train']

        # if data_args.max_predict_samples is not None:
            # predict_dataset = predict_dataset.select(range(data_args.max_predict_samples))

        with training_args.main_process_first(desc="prediction dataset map pre-processing"):
            predict_dataset = predict_dataset.map(
                preprocess_function,
                batched=True,
                num_proc=data_args.preprocessing_num_workers,
                remove_columns=column_names,
                load_from_cache_file=not data_args.overwrite_cache,
                desc="Running tokenizer on prediction dataset",
                fn_kwargs={
                            'eval_or_predict': True,
                            'use_sampleprompt': data_args.use_sampleprompt,}
            )
    
    max_length = (
        training_args.generation_max_length
        if training_args.generation_max_length is not None
        else data_args.val_max_target_length
    )

    model = BartForConditionalGeneration.from_pretrained(
        model_args.model_name_or_path,
        from_tf=bool(".ckpt" in model_args.model_name_or_path),
        config=config, # the model to projecting the target sequence to latent states, and projecting back
        cache_dir=model_args.cache_dir,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
        use_sampleprompt=data_args.use_sampleprompt,
        max_target_length=max_target_length,
    )

    model.resize_token_embeddings(len(tokenizer))

    # model.load_state_dict(torch.load("./bart_discharge_prompt/pytorch_model.bin"))

    # torch.save(model.state_dict(), "./bartbasemodel/bartlarge.pth", _use_new_zipfile_serialization=False)

    count_parameters(model, all_param=False)

    if model.config.decoder_start_token_id is None:
        raise ValueError("Make sure that `config.decoder_start_token_id` is correctly defined")

    if (
        hasattr(model.config, "max_position_embeddings")
        and model.config.max_position_embeddings < data_args.max_source_length
    ):
        if model_args.resize_position_embeddings is None:
            logger.warning(
                f"Increasing the model's number of position embedding vectors from {model.config.max_position_embeddings} "
                f"to {data_args.max_source_length}."
            )
            model.resize_position_embeddings(data_args.max_source_length)
        elif model_args.resize_position_embeddings:
            model.resize_position_embeddings(data_args.max_source_length)
        else:
            raise ValueError(
                f"`--max_source_length` is set to {data_args.max_source_length}, but the model only has {model.config.max_position_embeddings}"
                f" position encodings. Consider either reducing `--max_source_length` to {model.config.max_position_embeddings} or to automatically "
                "resize the model's position encodings by passing `--resize_position_embeddings`."
            )

    if training_args.label_smoothing_factor > 0 and not hasattr(model, "prepare_decoder_input_ids_from_labels"):
        logger.warning(
            "label_smoothing is enabled but the `prepare_decoder_input_ids_from_labels` method is not defined for"
            f"`{model.__class__.__name__}`. This will lead to loss being calculated twice and will take up more memory"
        )

    # Data collator
    label_pad_token_id = -100 if data_args.ignore_pad_token_for_loss else tokenizer.pad_token_id
    data_collator = DataCollatorForSeq2Seq(
        tokenizer,
        model=model,
        label_pad_token_id=label_pad_token_id,
        pad_to_multiple_of=8 if training_args.fp16 else None,
    )

    # Metric, from https://github.com/huggingface/datasets/blob/master/metrics/rouge/rouge.py
    metric = load_metric("./rouge_metric.py")

    def postprocess_text(preds, labels):
        preds = [pred.strip() for pred in preds]
        labels = [label.strip() for label in labels]
        # rougeLSum expects newline after each sentence
        preds = ["\n".join(nltk.sent_tokenize(pred)) for pred in preds]
        labels = ["\n".join(nltk.sent_tokenize(label)) for label in labels]

        return preds, labels

    def compute_metrics(eval_preds):
        preds, labels = eval_preds
        if isinstance(preds, tuple):
            preds = preds[0]
            
        preds[preds < 0] = tokenizer.pad_token_id

        decoded_preds = tokenizer.batch_decode(preds, skip_special_tokens=True)
        if data_args.ignore_pad_token_for_loss:
            # Replace -100 in the labels as we can't decode them.
            labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
        decoded_labels = tokenizer.batch_decode(labels, skip_special_tokens=True)

        # Some simple post-processing
        decoded_preds, decoded_labels = postprocess_text(decoded_preds, decoded_labels)

        result = metric.compute(predictions=decoded_preds, references=decoded_labels, use_stemmer=True)

        # Extract a few results from ROUGE
        for key, value in result.items():
            try:
                result[key] = value.mid.fmeasure * 100
            except:
                result[key] = value
        # result = {key: value.mid.fmeasure * 100 for key, value in result.items()}

        prediction_lens = [np.count_nonzero(pred != tokenizer.pad_token_id) for pred in preds]
        result["gen_len"] = np.mean(prediction_lens)
        result = {k: round(v, 6) for k, v in result.items()}
        return result

    # Initialize our Trainer
    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset if training_args.do_train else None,
        eval_dataset=eval_dataset if training_args.do_eval else None,
        tokenizer=tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics if training_args.predict_with_generate else None,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=20)]
    )

    # Training
    if training_args.do_train:
        checkpoint = None
        if training_args.resume_from_checkpoint is not None:
            checkpoint = training_args.resume_from_checkpoint
        elif last_checkpoint is not None:
            checkpoint = last_checkpoint

        train_result = trainer.train(resume_from_checkpoint=checkpoint)
        trainer.save_model()  # Saves the tokenizer too for easy upload

        metrics = train_result.metrics
        max_train_samples = (
            data_args.max_train_samples if data_args.max_train_samples is not None else len(train_dataset)
        )
        metrics["train_samples"] = min(max_train_samples, len(train_dataset))

        trainer.log_metrics("train", metrics)
        trainer.save_metrics("train", metrics)
        trainer.save_state()

    # Evaluation
    
    # num_beams = data_args.num_beams if data_args.num_beams is not None else training_args.generation_num_beams
    # above is original code, we update num_beams and max_length for full evalution and test
    num_beams = training_args.evaltest_generation_num_beams
    max_length = training_args.evaltest_generation_max_length

    if training_args.do_eval:
        logger.info("*** Evaluate ***")
        metrics = trainer.evaluate(max_length=max_length, num_beams=num_beams, metric_key_prefix="eval")
        max_eval_samples = data_args.max_eval_samples if data_args.max_eval_samples is not None else len(eval_dataset)
        metrics["eval_samples"] = min(max_eval_samples, len(eval_dataset))

        trainer.log_metrics("eval", metrics)
        trainer.save_metrics("eval", metrics)

    if training_args.do_predict:
        logger.info("*** Predict ***")

        predict_results = trainer.predict(
            predict_dataset, metric_key_prefix="predict", max_length=max_length, num_beams=num_beams
        )
        metrics = predict_results.metrics
        max_predict_samples = (
            data_args.max_predict_samples if data_args.max_predict_samples is not None else len(predict_dataset)
        )
        metrics["predict_samples"] = min(max_predict_samples, len(predict_dataset))

        trainer.log_metrics("predict", metrics)
        trainer.save_metrics("predict", metrics)

        if trainer.is_world_process_zero():
            if training_args.predict_with_generate:
                predictions = tokenizer.batch_decode(
                    predict_results.predictions, skip_special_tokens=True, clean_up_tokenization_spaces=True
                )
                predictions = [pred.strip() for pred in predictions]
                output_prediction_file = os.path.join(training_args.output_dir, "generated_predictions.txt")
                with open(output_prediction_file, "w", encoding='utf-8') as writer:
                    to_write_str = ''
                    for to_write in range(len(predictions)):
                        to_write_str += str(predictions[to_write])
                        to_write_str += "\n"
                        if (to_write + 1 == len(predictions)):
                            to_write_str += "\n"
                    writer.write(to_write_str)

                to_write_dict = dict()
                for _pred in predictions:
                    to_write_dict[len(to_write_dict)] = _pred
                json_name = "gens.json"
                with open(os.path.join(training_args.output_dir, json_name), 'w', encoding='utf-8') as write_f:
                    write_f.write(json.dumps(to_write_dict))

    if training_args.push_to_hub:
        kwargs = {"finetuned_from": model_args.model_name_or_path, "tasks": "summarization"}
        if data_args.dataset_name is not None:
            kwargs["dataset_tags"] = data_args.dataset_name
            if data_args.dataset_config_name is not None:
                kwargs["dataset_args"] = data_args.dataset_config_name
                kwargs["dataset"] = f"{data_args.dataset_name} {data_args.dataset_config_name}"
            else:
                kwargs["dataset"] = data_args.dataset_name

        trainer.push_to_hub(**kwargs)
    
    # model = BartForConditionalGeneration.from_pretrained(
    #     training_args.output_dir,
    #     from_tf=bool(".ckpt" in model_args.model_name_or_path),
    #     config=config,
    #     cache_dir=model_args.cache_dir,
    #     revision=model_args.model_revision,
    #     use_auth_token=True if model_args.use_auth_token else None,
    #     # tokenizer=tokenizer,
    # )

    return

if __name__ == '__main__':
    run_bart()
