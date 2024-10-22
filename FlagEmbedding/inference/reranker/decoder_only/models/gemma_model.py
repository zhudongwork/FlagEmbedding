#           🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨
#               This file was automatically generated from <path_to_diff_file.py>.
#         Do NOT edit this file manually as any edits will be overwritten by the generation of
#         the file from the diff. If any change should be done, please apply the change to the
#                                    diff.py file directly.
#           🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨
# coding=utf-8
# Copyright 2024 Google Inc. HuggingFace Inc. team. All rights reserved.
#
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from dataclasses import dataclass

import math
from typing import List, Optional, Tuple, Union

import inspect
import torch
import torch.nn.functional as F
import torch.utils.checkpoint
from torch import nn
from torch.nn import BCEWithLogitsLoss, CrossEntropyLoss, MSELoss

from transformers.activations import ACT2FN
from transformers.cache_utils import Cache, DynamicCache, StaticCache
from transformers.modeling_attn_mask_utils import AttentionMaskConverter
from transformers.modeling_outputs import (
    BaseModelOutputWithPast,
    CausalLMOutputWithPast,
    SequenceClassifierOutputWithPast,
    TokenClassifierOutput,
)
from transformers.modeling_utils import PreTrainedModel
from transformers.pytorch_utils import ALL_LAYERNORM_LAYERS
from transformers.utils import (
    add_start_docstrings,
    add_start_docstrings_to_model_forward,
    is_flash_attn_2_available,
    is_flash_attn_greater_or_equal_2_10,
    logging,
    replace_return_docstrings,
    ModelOutput,
)
from .gemma_config import CostWiseGemmaConfig
from transformers.models.gemma2.modeling_gemma2 import Gemma2RMSNorm, Gemma2RotaryEmbedding, rotate_half, apply_rotary_pos_emb
from transformers.models.gemma2.modeling_gemma2 import Gemma2MLP, repeat_kv, Gemma2Attention, Gemma2FlashAttention2, Gemma2SdpaAttention, GEMMA2_ATTENTION_CLASSES, Gemma2DecoderLayer, GEMMA2_START_DOCSTRING
from transformers.models.gemma2.modeling_gemma2 import GEMMA2_INPUTS_DOCSTRING

if is_flash_attn_2_available():
    from flash_attn import flash_attn_func, flash_attn_varlen_func
    from flash_attn.bert_padding import index_first_axis, pad_input, unpad_input  # noqa

    _flash_supports_window_size = "window_size" in list(inspect.signature(flash_attn_func).parameters)


logger = logging.get_logger(__name__)


def _get_unpad_data(attention_mask):
    seqlens_in_batch = attention_mask.sum(dim=-1, dtype=torch.int32)
    indices = torch.nonzero(attention_mask.flatten(), as_tuple=False).flatten()
    max_seqlen_in_batch = seqlens_in_batch.max().item()
    cu_seqlens = F.pad(torch.cumsum(seqlens_in_batch, dim=0, dtype=torch.int32), (1, 0))
    return (
        indices,
        cu_seqlens,
        max_seqlen_in_batch,
    )

@add_start_docstrings(
    "The bare Gemma2 Model outputting raw hidden-states without any specific head on top.",
    GEMMA2_START_DOCSTRING,
)
class CostWiseGemma2PreTrainedModel(PreTrainedModel):
    config_class = CostWiseGemmaConfig
    base_model_prefix = "model"
    supports_gradient_checkpointing = True
    _no_split_modules = ["Gemma2DecoderLayer"]
    _skip_keys_device_placement = ["past_key_values"]
    _supports_flash_attn_2 = True
    _supports_sdpa = True
    _supports_cache_class = False
    _supports_quantized_cache = False
    _supports_static_cache = True
    _is_stateful = True

    def _init_weights(self, module):
        std = self.config.initializer_range
        if isinstance(module, nn.Linear):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()

GEMMA2_ATTENTION_CLASSES = {
    "eager": Gemma2Attention,
    "flash_attention_2": Gemma2FlashAttention2,
    "sdpa": Gemma2SdpaAttention,
}


_CONFIG_FOR_DOC = "CostWiseGemmaConfig"

@dataclass
class CostWiseModelOutputWithPast(ModelOutput):
    last_hidden_state: torch.FloatTensor = None
    past_key_values: Optional[Tuple[Tuple[torch.FloatTensor]]] = None
    hidden_states: Optional[Tuple[torch.FloatTensor]] = None
    attentions: Optional[Tuple[torch.FloatTensor]] = None
    attention_masks: Optional[Tuple[torch.FloatTensor]] = None

@dataclass
class CostWiseCausalLMOutputWithPast(ModelOutput):
    loss: Optional[torch.FloatTensor] = None
    logits: torch.FloatTensor = None
    past_key_values: Optional[Tuple[Tuple[torch.FloatTensor]]] = None
    hidden_states: Optional[Tuple[torch.FloatTensor]] = None
    attentions: Optional[Tuple[torch.FloatTensor]] = None
    attention_masks: Optional[Tuple[torch.FloatTensor]] = None

def token_compress(compress_ratio,
                   hidden_states,
                   attention_mask,
                   query_lengths,
                   prompt_lengths):
    """
        compress_ratio: int
        hidden_states: (b, s, h)
        attention_mask: (b, s)
        query_lengths: (b)
        prompt_lengths: (b)
    """
    # get some specific parameters
    passage_lengths = torch.sum(attention_mask, dim=1, dtype=torch.int) - query_lengths - prompt_lengths # the raw passage lengths (b)
    retain_passage_lengths = (passage_lengths + compress_ratio - 1) // compress_ratio # the passage lengths need to be retained (b)
    final_useful_lengths = query_lengths + prompt_lengths + retain_passage_lengths # the final useful length after compress (b)
    max_passage_length = torch.max(passage_lengths) # the max passage lengths (1)
    max_final_lengths = torch.max(final_useful_lengths) # the max useful lengths after compress (1)
    # make new hidden states and new attention masks
    new_hidden_states = torch.zeros((hidden_states.shape[0], max_final_lengths,
                                     hidden_states.shape[-1]), dtype=hidden_states.dtype).to(hidden_states.device) # (b, s', h)
    new_attention_mask = torch.ones((hidden_states.shape[0], max_final_lengths), dtype=attention_mask.dtype).to(attention_mask.device) # (b, s')
    # get new attention mask
    mask_attention_index = torch.arange(max_final_lengths, device=hidden_states.device).unsqueeze(0) >= final_useful_lengths[:, None]
    new_attention_mask[mask_attention_index] = 0
    # get new hidden states
    # add query into new hidden states
    query_index = torch.arange(max_final_lengths, device=hidden_states.device).unsqueeze(0)
    mask_query_index = query_index < query_lengths[:, None]
    new_hidden_states[mask_query_index] = hidden_states[:, : max_final_lengths, :][mask_query_index]
    # add prompt into new hidden states
    # get the index of the prompt in new hidden states
    new_prompt_start_length = query_lengths + retain_passage_lengths
    new_prompt_end_length = new_prompt_start_length + prompt_lengths
    new_prompt_index = torch.arange(max_final_lengths, device=hidden_states.device).unsqueeze(0)
    new_mask_prompt_index_start = new_prompt_index >= new_prompt_start_length[:, None]
    new_mask_prompt_index_end = new_prompt_index < new_prompt_end_length[:, None]
    new_mask_prompt_index = new_mask_prompt_index_start & new_mask_prompt_index_end
    # get the index of the prompt in hidden states
    raw_prompt_start_length = query_lengths + passage_lengths
    raw_prompt_end_length = raw_prompt_start_length + prompt_lengths
    raw_prompt_index = torch.arange(hidden_states.shape[1], device=hidden_states.device).unsqueeze(0)
    raw_mask_prompt_index_start = raw_prompt_index >= raw_prompt_start_length[:, None]
    raw_mask_prompt_index_end = raw_prompt_index < raw_prompt_end_length[:, None]
    raw_mask_prompt_index = raw_mask_prompt_index_start & raw_mask_prompt_index_end
    # replace the prompt hidden states
    new_hidden_states[new_mask_prompt_index] = hidden_states[raw_mask_prompt_index]
    # 以上均没问题

    # print(new_hidden_states.view(len(new_hidden_states), -1))
    # print(new_attention_mask)

    # get the index of the passage in new hidden states
    new_passage_start_length = query_lengths
    new_passage_end_length = new_passage_start_length + retain_passage_lengths
    new_passage_index = torch.arange(max_final_lengths, device=hidden_states.device).unsqueeze(0)
    new_mask_passage_index_start = new_passage_index >= new_passage_start_length[:, None]
    new_mask_passage_index_end = new_passage_index < new_passage_end_length[:, None]
    new_mask_passage_index = new_mask_passage_index_start & new_mask_passage_index_end
    # print(query_lengths, prompt_lengths, retain_passage_lengths, final_useful_lengths)
    # add passage into new hidden states
    # get mask hidden states
    psg_start_length = query_lengths
    psg_end_length = query_lengths + passage_lengths
    psg_index = torch.arange(hidden_states.shape[1], device=hidden_states.device).unsqueeze(0)
    mask_psg_index_start = psg_index >= psg_start_length[:, None]
    mask_psg_index_end = psg_index < psg_end_length[:, None]
    mask_psg_index = mask_psg_index_start & mask_psg_index_end

    hidden_states = hidden_states * mask_psg_index.unsqueeze(-1)
    passage_hidden_states = torch.zeros((hidden_states.shape[0],
                                         (max_passage_length + compress_ratio - 1) // compress_ratio * compress_ratio,
                                         hidden_states.shape[-1]), dtype=hidden_states.dtype).to(hidden_states.device)
    passage_end_length = passage_lengths
    passage_index = torch.arange(passage_hidden_states.shape[1], device=hidden_states.device).unsqueeze(0) # maybe exceed the max passage length
    mask_passage_index = passage_index < passage_end_length[:, None]

    raw_passage_end_length = query_lengths + passage_lengths
    raw_passage_start_length = query_lengths
    raw_passage_index = torch.arange(hidden_states.shape[1], device=hidden_states.device).unsqueeze(0)
    raw_mask_passage_index_start = raw_passage_index >= raw_passage_start_length[:, None]
    raw_mask_passage_index_end = raw_passage_index < raw_passage_end_length[:, None]
    raw_mask_passage_index = raw_mask_passage_index_start & raw_mask_passage_index_end
    passage_hidden_states[mask_passage_index] = hidden_states[raw_mask_passage_index]

    passage_weights = torch.zeros((hidden_states.shape[0],
                                   (max_passage_length + compress_ratio - 1) // compress_ratio * compress_ratio)
                                  , dtype=hidden_states.dtype).to(hidden_states.device)
    passage_weights[mask_passage_index] = 1
    passage_weights = passage_weights.view(passage_weights.shape[0], -1, compress_ratio)
    passage_weights = passage_weights / torch.sum(passage_weights, dim=-1
                                                  ).view(passage_weights.shape[0], -1, 1)
    passage_weights = passage_weights.view(passage_weights.shape[0], -1)
    # passage_weights = torch.where(passage_weights == torch.nan, 0, passage_weights)
    passage_hidden_states = passage_hidden_states * passage_weights.unsqueeze(-1)
    passage_hidden_states = passage_hidden_states.view(passage_hidden_states.shape[0], -1, compress_ratio,
                                                       passage_hidden_states.shape[-1])
    passage_hidden_states = torch.sum(passage_hidden_states, dim=2)
    passage_end_length = retain_passage_lengths
    passage_index = torch.arange(passage_hidden_states.shape[1], device=hidden_states.device).unsqueeze(0)
    mask_passage_index = passage_index < passage_end_length[:, None]
    new_hidden_states[new_mask_passage_index] = passage_hidden_states[mask_passage_index]

    return new_hidden_states, new_attention_mask

@add_start_docstrings(
    "The bare Gemma2 Model outputting raw hidden-states without any specific head on top.",
    GEMMA2_START_DOCSTRING,
)
class CostWiseGemmaModel(CostWiseGemma2PreTrainedModel):
    """
    Transformer decoder consisting of *config.num_hidden_layers* layers. Each layer is a [`GemmaDecoderLayer`]

    Args:
        config: GemmaConfig
    """

    def __init__(self, config: CostWiseGemmaConfig):
        super().__init__(config)
        self.padding_idx = config.pad_token_id
        self.vocab_size = config.vocab_size

        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size, self.padding_idx)
        self.layers = nn.ModuleList(
            [Gemma2DecoderLayer(config, layer_idx) for layer_idx in range(config.num_hidden_layers)]
        )
        self.norm = Gemma2RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.gradient_checkpointing = False

        # Initialize weights and apply final processing
        self.post_init()

    def get_input_embeddings(self):
        return self.embed_tokens

    def set_input_embeddings(self, value):
        self.embed_tokens = value

    @add_start_docstrings_to_model_forward(GEMMA2_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids: torch.LongTensor = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        past_key_values: Optional[Union[Cache, List[torch.FloatTensor]]] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        use_cache: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
        cache_position: Optional[torch.LongTensor] = None,
        compress_layer: Optional[int] = None,
        compress_ratio: Optional[int] = None,
        cutoff_layers: Optional[List[int]] = None,
        query_lengths: Optional[int] = None,
        prompt_lengths: Optional[int] = None,
    ) -> Union[Tuple, CostWiseModelOutputWithPast]:
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions

        compress_ratio = None if compress_ratio == 1 else compress_ratio

        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )
        if self.config.layer_wise:
            output_hidden_states = True

        use_cache = use_cache if use_cache is not None else self.config.use_cache
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        if (input_ids is None) ^ (inputs_embeds is not None):
            raise ValueError(
                "You cannot specify both input_ids and inputs_embeds at the same time, and must specify either one"
            )

        if self.gradient_checkpointing and self.training and use_cache:
            logger.warning_once(
                "`use_cache=True` is incompatible with gradient checkpointing. Setting `use_cache=False`."
            )
            use_cache = False

        if compress_layer is not None and compress_ratio is not None:
            logger.warning_once(
                "`use_cache=True` is incompatible with reranker. Setting `use_cache=False`."
            )
            use_cache = False

        if inputs_embeds is None:
            inputs_embeds = self.embed_tokens(input_ids)

        if cache_position is None:
            cache_position = torch.arange(0, inputs_embeds.shape[1], device=inputs_embeds.device)

        if position_ids is None:
            position_ids = cache_position.unsqueeze(0)

        causal_mask = self._update_causal_mask(
            attention_mask, inputs_embeds, cache_position, past_key_values, output_attentions
        )

        # embed positions
        hidden_states = inputs_embeds

        # normalized
        # Gemma downcasts the below to float16, causing sqrt(3072)=55.4256 to become 55.5
        # See https://github.com/huggingface/transformers/pull/29402
        normalizer = torch.tensor(self.config.hidden_size**0.5, dtype=hidden_states.dtype)
        hidden_states = hidden_states * normalizer

        # decoder layers
        all_hidden_states = () if output_hidden_states else None
        all_attention_masks = ()
        all_self_attns = () if output_attentions else None
        next_decoder_cache = None

        is_padding_left = (attention_mask[:, -1].sum() == attention_mask.shape[0]) and (
                torch.sum(attention_mask) != attention_mask.shape[0] * attention_mask.shape[1])
        query_lengths = [0] * hidden_states.shape[0] if query_lengths is None else query_lengths
        prompt_lengths = [0] * hidden_states.shape[0] if prompt_lengths is None else prompt_lengths
        if not isinstance(query_lengths, torch.Tensor):
            query_lengths = torch.tensor(query_lengths, device=hidden_states.device)
        if not isinstance(prompt_lengths, torch.Tensor):
            prompt_lengths = torch.tensor(prompt_lengths, device=hidden_states.device)

        if cutoff_layers is None:
            max_layer = self.config.num_hidden_layers
            cutoff_layers = [max_layer]
        if isinstance(cutoff_layers, int):
            max_layer = cutoff_layers
            cutoff_layers = [cutoff_layers]
        else:
            max_layer = max(cutoff_layers)

        for idx, decoder_layer in enumerate(self.layers):
            if self.config.layer_wise:
                if idx in cutoff_layers and output_hidden_states:
                    all_hidden_states += (self.norm(hidden_states),)
                    all_attention_masks += (attention_mask,)
                if idx == max_layer:
                    break
            elif output_hidden_states:
                all_hidden_states += (hidden_states,)

            if compress_layer is not None and compress_ratio is not None and idx in compress_layer and idx != 0:
                if is_padding_left:
                    raise ValueError('You must use right padding...')
                hidden_states, attention_mask = token_compress(compress_ratio, hidden_states, attention_mask,
                                                               query_lengths, prompt_lengths)
                seq_length = hidden_states.shape[1]
                cache_position = torch.arange(0, seq_length, device=hidden_states.device)
                position_ids = cache_position.unsqueeze(0)
                causal_mask = self._update_causal_mask(
                    attention_mask, hidden_states, cache_position, past_key_values, output_attentions
                )

            if self.gradient_checkpointing and self.training:
                layer_outputs = self._gradient_checkpointing_func(
                    decoder_layer.__call__,
                    hidden_states,
                    causal_mask,
                    position_ids,
                    past_key_values,
                    output_attentions,
                    use_cache,
                    cache_position,
                )
            else:
                layer_outputs = decoder_layer(
                    hidden_states,
                    attention_mask=causal_mask,
                    position_ids=position_ids,
                    past_key_value=past_key_values,
                    output_attentions=output_attentions,
                    use_cache=use_cache,
                    cache_position=cache_position,
                )

            hidden_states = layer_outputs[0]

            if output_attentions:
                all_self_attns += (layer_outputs[1],)

        hidden_states = self.norm(hidden_states)

        # add hidden states from the last decoder layer
        if not self.config.layer_wise:
            if output_hidden_states:
                all_hidden_states += (hidden_states,)
                all_attention_masks += (attention_mask,)
        else:
            if output_hidden_states and self.config.num_hidden_layers == max_layer:
                all_hidden_states += (hidden_states,)
                all_attention_masks += (attention_mask,)

        next_cache = next_decoder_cache if use_cache else None

        if not return_dict:
            return tuple(v for v in [hidden_states, next_cache, all_hidden_states, all_self_attns] if v is not None)
        return CostWiseModelOutputWithPast(
            last_hidden_state=hidden_states,
            past_key_values=next_cache,
            hidden_states=all_hidden_states,
            attentions=all_self_attns,
            attention_masks=all_attention_masks
        )

    def _update_causal_mask(
        self,
        attention_mask: torch.Tensor,
        input_tensor: torch.Tensor,
        cache_position: torch.Tensor,
        past_key_values: Cache,
        output_attentions: bool,
    ):
        if self.config._attn_implementation == "flash_attention_2":
            if attention_mask is not None and 0.0 in attention_mask:
                return attention_mask
            return None

        dtype, device = input_tensor.dtype, input_tensor.device
        min_dtype = torch.finfo(dtype).min
        sequence_length = input_tensor.shape[1]
        if past_key_values is not None:
            target_length = past_key_values.get_max_length()
        else:
            target_length = attention_mask.shape[-1] if attention_mask is not None else input_tensor.shape[1]

        if attention_mask is not None and attention_mask.dim() == 4:
            # in this case we assume that the mask comes already in inverted form and requires no inversion or slicing
            if attention_mask.max() != 0:
                raise ValueError("Custom 4D attention mask should be passed in inverted form with max==0`")
            causal_mask = attention_mask
        else:
            causal_mask = torch.full(
                (sequence_length, target_length), fill_value=min_dtype, dtype=dtype, device=device
            )
            if sequence_length != 1:
                causal_mask = torch.triu(causal_mask, diagonal=1)
            causal_mask *= torch.arange(target_length, device=device) > cache_position.reshape(-1, 1)
            causal_mask = causal_mask[None, None, :, :].expand(input_tensor.shape[0], 1, -1, -1)
            if attention_mask is not None:
                causal_mask = causal_mask.clone()  # copy to contiguous memory for in-place edit
                mask_length = attention_mask.shape[-1]
                padding_mask = causal_mask[:, :, :, :mask_length] + attention_mask[:, None, None, :]
                padding_mask = padding_mask == 0
                causal_mask[:, :, :, :mask_length] = causal_mask[:, :, :, :mask_length].masked_fill(
                    padding_mask, min_dtype
                )
        return causal_mask


class CostWiseHead(nn.Module):
    """Head for sentence-level classification tasks."""

    def __init__(self, input_size, output_size):
        super().__init__()
        self.linear_head = nn.Linear(input_size, output_size, bias=False)

    def forward(self, **kwargs):
        return self.linear_head(**kwargs)


class CostWiseGemmaForCausalLM(CostWiseGemma2PreTrainedModel):
    _tied_weights_keys = ["lm_head.weight"]

    def __init__(self, config: CostWiseGemmaConfig):
        super().__init__(config)
        self.model = CostWiseGemmaModel(config)
        self.vocab_size = config.vocab_size

        if not config.layer_wise:
            self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        else:
            self.lm_head = nn.ModuleList(
                [CostWiseHead(config.hidden_size, 1) for _ in range(
                    config.start_layer, config.num_hidden_layers + 1, config.layer_sep
                )]
            )

        # Initialize weights and apply final processing
        self.post_init()

    def get_input_embeddings(self):
        return self.model.embed_tokens

    def set_input_embeddings(self, value):
        self.model.embed_tokens = value

    def get_output_embeddings(self):
        return self.lm_head

    def set_output_embeddings(self, new_embeddings):
        self.lm_head = new_embeddings

    def set_decoder(self, decoder):
        self.model = decoder

    def get_decoder(self):
        return self.model

    @add_start_docstrings_to_model_forward(GEMMA2_INPUTS_DOCSTRING)
    @replace_return_docstrings(output_type=CausalLMOutputWithPast, config_class=_CONFIG_FOR_DOC)
    def forward(
        self,
        input_ids: torch.LongTensor = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        past_key_values: Optional[Union[Cache, List[torch.FloatTensor]]] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        labels: Optional[torch.LongTensor] = None,
        use_cache: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
        cache_position: Optional[torch.LongTensor] = None,
        compress_layer: Optional[int] = None,
        compress_ratio: Optional[int] = None,
        cutoff_layers: Optional[List[int]] = None,
        query_lengths: Optional[int] = None,
        prompt_lengths: Optional[int] = None,
    ) -> Union[Tuple, CostWiseCausalLMOutputWithPast]:
        r"""
        Args:
            labels (`torch.LongTensor` of shape `(batch_size, sequence_length)`, *optional*):
                Labels for computing the masked language modeling loss. Indices should either be in `[0, transformers.,
                config.vocab_size]` or -100 (see `input_ids` docstring). Tokens with indices set to `-100` are ignored
                (masked), the loss is only computed for the tokens with labels in `[0, transformers., config.vocab_size]`.

        Returns:

        Example:

         ```python
        >>> from transformers import AutoTokenizer, GemmaForCausalLM

        >>> model = GemmaForCausalLM.from_pretrained("google/gemma-2-9b")
        >>> tokenizer = AutoTokenizer.from_pretrained("google/gemma-2-9b")

        >>> prompt = "What is your favorite condiment?"
        >>> inputs = tokenizer(prompt, return_tensors="pt")

        >>> # Generate
        >>> generate_ids = model.generate(inputs.input_ids, max_length=30)
        >>> tokenizer.batch_decode(generate_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
        "What is your favorite condiment?"
        ```"""
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        if compress_ratio is not None and compress_ratio == 1:
            compress_ratio = None

        if self.config.layer_wise:
            if cutoff_layers is None:
                cutoff_layers = [self.config.num_hidden_layers]
            elif isinstance(cutoff_layers, int):
                cutoff_layers = [cutoff_layers]
            can_use_layers = list(range(self.config.start_layer, self.config.num_hidden_layers + 1, self.config.layer_sep))
            remove_layers = [i for i in cutoff_layers if i not in can_use_layers]
            if len(remove_layers) > 0:
                logger.warning_once(
                    f"layers {remove_layers} are incompatible with the setting. They will be removed..."
                )
            cutoff_layers = [i for i in cutoff_layers if i not in remove_layers]
            if len(cutoff_layers) == 0:
                raise ValueError(f"Your cutoff layers must in [{self.config.start_layer}, {self.config.num_hidden_layers}]")

        # decoder outputs consists of (dec_features, layer_state, dec_hidden, dec_attn)
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            use_cache=use_cache,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
            cache_position=cache_position,
            compress_layer=compress_layer,
            compress_ratio=compress_ratio,
            query_lengths=query_lengths,
            prompt_lengths=prompt_lengths,
            cutoff_layers=cutoff_layers,
        )

        if not self.config.layer_wise:
            hidden_states = outputs[0]
            logits = self.lm_head(hidden_states)
            if self.config.final_logit_softcapping is not None:
                logits = logits / self.config.final_logit_softcapping
                logits = torch.tanh(logits)
                logits = logits * self.config.final_logit_softcapping
            logits = logits.float()
            loss = None
            if labels is not None:
                # Shift so that tokens < n predict n
                shift_logits = logits[..., :-1, :].contiguous()
                shift_labels = labels[..., 1:].contiguous()
                # Flatten the tokens
                loss_fct = CrossEntropyLoss()
                shift_logits = shift_logits.view(-1, self.config.vocab_size)
                shift_labels = shift_labels.view(-1)
                # Enable model parallelism
                shift_labels = shift_labels.to(shift_logits.device)
                loss = loss_fct(shift_logits, shift_labels)
        else:
            hidden_states = outputs.hidden_states
            logits = ()
            for i in range(len(hidden_states)):
                tmp_logits = self.lm_head[i].linear_head(hidden_states[i])
                if self.config.final_logit_softcapping is not None:
                    tmp_logits = tmp_logits / self.config.final_logit_softcapping
                    tmp_logits = torch.tanh(tmp_logits)
                    tmp_logits = tmp_logits * self.config.final_logit_softcapping
                tmp_logits = tmp_logits.float()
                tmp_logits = tmp_logits.reshape(hidden_states[i].shape[0], -1)
                logits = logits + (tmp_logits,)
            loss = None

        if not return_dict:
            output = (logits,) + outputs[1:]
            return (loss,) + output if loss is not None else output

        return CostWiseCausalLMOutputWithPast(
            loss=loss,
            logits=logits,
            past_key_values=outputs.past_key_values,
            hidden_states=outputs.hidden_states,
            attentions=outputs.attentions,
            attention_masks=outputs[-1] if self.model.config.layer_wise else outputs[-1][-1]
        )

    def prepare_inputs_for_generation(
        self,
        input_ids,
        past_key_values=None,
        attention_mask=None,
        inputs_embeds=None,
        cache_position=None,
        use_cache=True,
        **kwargs,
    ):
        past_length = 0
        if past_key_values is not None:
            # Past key values are always initialized with a `Cache` object -> no need for if-else anymore
            past_length = cache_position[0] if cache_position is not None else torch.tensor(0, device=input_ids.device)
            max_cache_length = (
                torch.tensor(past_key_values.get_max_length(), device=input_ids.device)
                if past_key_values.get_max_length() is not None
                else None
            )
            cache_length = past_length if max_cache_length is None else torch.min(max_cache_length, past_length)

            # Keep only the unprocessed tokens:
            # 1 - If the length of the attention_mask exceeds the length of input_ids, then we are in a setting where
            # some of the inputs are exclusively passed as part of the cache (e.g. when passing input_embeds as input)
            if attention_mask is not None and attention_mask.shape[1] > input_ids.shape[1]:
                input_ids = input_ids[:, -(attention_mask.shape[1] - past_length) :]
            # 2 - If the past_length is smaller than input_ids', then input_ids holds all input tokens. We can discard
            # input_ids based on the past_length.
            elif past_length < input_ids.shape[1]:
                input_ids = input_ids[:, past_length:]
            # 3 - Otherwise (past_length >= input_ids.shape[1]), let's assume input_ids only has unprocessed tokens.

            # If we are about to go beyond the maximum cache length, we need to crop the input attention mask.
            if (
                max_cache_length is not None
                and attention_mask is not None
                and cache_length + input_ids.shape[1] > max_cache_length
            ):
                attention_mask = attention_mask[:, -max_cache_length:]

        position_ids = kwargs.get("position_ids", None)
        if attention_mask is not None and position_ids is None:
            # create position_ids on the fly for batch generation
            position_ids = attention_mask.long().cumsum(-1) - 1
            position_ids.masked_fill_(attention_mask == 0, 1)
            if past_key_values:
                position_ids = position_ids[:, -input_ids.shape[1] :]

        # if `inputs_embeds` are passed, we only want to use them in the 1st generation step
        if inputs_embeds is not None and past_length == 0:
            model_inputs = {"inputs_embeds": inputs_embeds}
        else:
            # The `contiguous()` here is necessary to have a static stride during decoding. torchdynamo otherwise
            # recompiles graphs as the stride of the inputs is a guard. Ref: https://github.com/huggingface/transformers/pull/29114
            # TODO: use `next_tokens` directly instead.
            model_inputs = {"input_ids": input_ids.contiguous()}

        input_length = position_ids.shape[-1] if position_ids is not None else input_ids.shape[-1]
        if cache_position is None:
            cache_position = torch.arange(past_length, past_length + input_length, device=input_ids.device)
        elif use_cache:
            cache_position = cache_position[-input_length:]

        model_inputs.update(
            {
                "position_ids": position_ids,
                "cache_position": cache_position,
                "past_key_values": past_key_values,
                "use_cache": use_cache,
                "attention_mask": attention_mask,
            }
        )
        return model_inputs

    @staticmethod
    def _reorder_cache(past_key_values, beam_idx):
        reordered_past = ()
        for layer_past in past_key_values:
            reordered_past += (
                tuple(past_state.index_select(0, beam_idx.to(past_state.device)) for past_state in layer_past),
            )
        return reordered_past