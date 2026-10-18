import os
import re
from functools import lru_cache

import sqlglot
import torch
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFacePipeline
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

load_dotenv()

# Must match the format used in fine-tuning. Edit here if your training prompt differs.
PROMPT = PromptTemplate.from_template("Schema: {schema}\nInstruction: {instruction}\nSQL:\n")


@lru_cache(maxsize=1)
def _llms():
    base = os.environ["BASE_MODEL_PATH"]
    adapter = os.environ["LORA_ADAPTER_PATH"]

    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.float32)
    model = PeftModel.from_pretrained(model, adapter)
    model.eval()

    def make(**kw):
        pipe = pipeline(
            "text-generation", model=model, tokenizer=tok,
            max_new_tokens=200, return_full_text=False,
            pad_token_id=tok.eos_token_id, **kw,
        )
        return HuggingFacePipeline(pipeline=pipe)

    greedy = make(do_sample=False)                   # first attempt: deterministic
    sampled = make(do_sample=True, temperature=0.7)  # retries: vary the output
    return greedy, sampled


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:sql)?", "", text).strip()
    if ";" in text:
        text = text[: text.index(";") + 1]
    return text.strip()


def _to_dialect(sql: str, target: str) -> str:
    """Training SQL is SQLite-style; convert to the target database dialect."""
    if target == "sqlite":
        return sql
    try:
        return sqlglot.transpile(sql, read="sqlite", write=target)[0]
    except Exception:
        return sql  # validator will catch it if it's unusable


def generate_sql(instruction: str, schema_text: str, target_dialect: str, attempt: int = 0) -> str:
    greedy, sampled = _llms()
    chain = PROMPT | (sampled if attempt > 0 else greedy) | StrOutputParser()
    raw = chain.invoke({"schema": schema_text, "instruction": instruction})
    return _to_dialect(_clean(raw), target_dialect)