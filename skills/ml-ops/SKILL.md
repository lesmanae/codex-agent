# ML / Data Science Ops — Skill

**Trigger phrases**: machine learning, ml, ai, model, train, training, fine-tune,
finetune, lora, qlora, peft, inference, predict, classification, regression,
clustering, embedding, embed, vector, similarity, cosine, recommendation,
torch, pytorch, tensorflow, keras, jax, sklearn, scikit-learn, xgboost, lightgbm,
catboost, transformers, huggingface, diffusers, langchain, llamaindex, llama.cpp,
ollama, vllm, sglang, gguf, quantize, quantization, gpu, cuda, cudnn, rocm,
cpu inference, dataset, dataloader, dataframe, pandas, polars, numpy, scipy,
matplotlib, seaborn, plotly, jupyter, kaggle, colab, mlflow, wandb, tensorboard,
optuna, hyperparameter, learning rate, batch size, epoch, loss, accuracy,
precision, recall, f1, roc, auc, confusion matrix, overfit, underfit, regularize,
dropout, batchnorm, augmentation, data leakage, train test split, cross validation.

Use when the user asks to train, evaluate, fine-tune, or deploy ML/DL
models, do data analysis, or run inference. Audience: dev / researcher
on the VPS — emphasize **what runs on the available hardware** and **what
saves time vs. doing the wrong thing fast**.

---

## Operating principles

- **Detect the hardware first.** `nvidia-smi`, `lspci | grep -i nvidia`,
  `cat /proc/cpuinfo | grep -c ^processor`. Don't promise GPU training
  on a 2-core CPU box.
- **Start with a tiny dataset and a small model.** Get the loop green
  end-to-end before scaling up.
- **Always split train/val/test BEFORE feature engineering.** Anything
  fit on test is leakage.
- **Reproducible.** Set seeds (`torch.manual_seed(0)`,
  `np.random.seed(0)`, `random.seed(0)`); pin versions in
  `requirements.txt`/`pyproject.toml`.
- **Save checkpoints + metrics.** Even a single .pt + a metrics.json
  beats losing a 6-hour run.

---

## Hardware probe

```bash
nvidia-smi 2>/dev/null || echo "no NVIDIA GPU"
python3 -c "import torch; print('cuda:', torch.cuda.is_available(),
'device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu',
'bf16:', torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False)"
free -h | awk 'NR==2{print "RAM:", $2}'
```

If CPU-only and the user wants LLM inference: recommend `llama.cpp` /
`ollama` with quantized GGUF models (Q4_K_M for 7B fits in ~5GB RAM).

---

## Standard project skeleton

```
project/
  pyproject.toml
  src/
    data.py        # load + split + transform
    model.py       # the architecture
    train.py       # training loop
    eval.py        # held-out evaluation
    infer.py       # one-shot CLI inference
  configs/
    base.yaml
  data/
  checkpoints/
  metrics.json
```

## sklearn classifier in 30 seconds

```python
from sklearn.datasets import load_breast_cancer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report

X, y = load_breast_cancer(return_X_y=True)
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=0, stratify=y)

pipe = Pipeline([("sc", StandardScaler()), ("clf", LogisticRegression(max_iter=2000))])
pipe.fit(Xtr, ytr)
print(classification_report(yte, pipe.predict(Xte)))
```

## PyTorch training loop (minimal, working)

```python
import torch, torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

X = torch.randn(1024, 20); y = (X.sum(1) > 0).long()
loader = DataLoader(TensorDataset(X, y), batch_size=64, shuffle=True)

model = nn.Sequential(nn.Linear(20, 64), nn.ReLU(), nn.Linear(64, 2))
opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
loss_fn = nn.CrossEntropyLoss()

for epoch in range(5):
    total = 0
    for xb, yb in loader:
        opt.zero_grad()
        loss = loss_fn(model(xb), yb)
        loss.backward(); opt.step()
        total += loss.item() * len(xb)
    print(f"epoch {epoch} loss {total/len(loader.dataset):.4f}")
torch.save(model.state_dict(), "checkpoints/model.pt")
```

## HuggingFace Transformers — text classification finetune

```bash
pip install transformers datasets accelerate evaluate
```

```python
from datasets import load_dataset
from transformers import (AutoTokenizer, AutoModelForSequenceClassification,
                          TrainingArguments, Trainer)
import evaluate, numpy as np

ds = load_dataset("imdb").shuffle(seed=0)
ds["train"] = ds["train"].select(range(2000)); ds["test"] = ds["test"].select(range(500))
tok = AutoTokenizer.from_pretrained("distilbert-base-uncased")
ds = ds.map(lambda b: tok(b["text"], truncation=True, padding="max_length"), batched=True)

m = AutoModelForSequenceClassification.from_pretrained("distilbert-base-uncased", num_labels=2)
acc = evaluate.load("accuracy")
def metrics(eval_pred): p, l = eval_pred; return acc.compute(predictions=np.argmax(p, -1), references=l)

args = TrainingArguments(output_dir="out", per_device_train_batch_size=8,
                         num_train_epochs=1, eval_strategy="epoch", logging_steps=20)
Trainer(m, args, train_dataset=ds["train"], eval_dataset=ds["test"], compute_metrics=metrics).train()
```

## LoRA / QLoRA fine-tune (PEFT)

```bash
pip install peft bitsandbytes accelerate
```

```python
from peft import LoraConfig, get_peft_model, TaskType
cfg = LoraConfig(task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, lora_dropout=0.05,
                 target_modules=["q_proj","v_proj"])
model = get_peft_model(base_model, cfg)
model.print_trainable_parameters()    # → small % of total
```

## Local LLM inference (CPU-friendly)

```bash
# Ollama (easiest)
curl -fsSL https://ollama.com/install.sh | sh
ollama run llama3:8b "Hello"

# llama.cpp (manual, fastest CPU)
git clone https://github.com/ggerganov/llama.cpp; cd llama.cpp; make -j
./main -m /path/to/model-q4_k_m.gguf -p "Hello" -n 200 -t $(nproc)
```

## Embeddings + similarity (no GPU needed)

```python
from sentence_transformers import SentenceTransformer
m = SentenceTransformer("all-MiniLM-L6-v2")
docs = ["cat sat on mat", "dog ran fast", "fish swims"]
emb = m.encode(docs, normalize_embeddings=True)
import numpy as np
q = m.encode("kitten on rug", normalize_embeddings=True)
sim = emb @ q
print({d: float(s) for d, s in zip(docs, sim)})
```

For larger corpora use FAISS (`pip install faiss-cpu`).

---

## Experiment tracking

```bash
pip install mlflow
mlflow ui --host 0.0.0.0 --port 5000      # in tmux/systemd
```

```python
import mlflow
with mlflow.start_run():
    mlflow.log_params({"lr": 1e-3, "bs": 64})
    mlflow.log_metric("val_acc", 0.91)
    mlflow.pytorch.log_model(model, "model")
```

## Pitfalls

- Loading a CUDA model on a CPU-only box → cryptic `Torch not compiled
  with CUDA enabled` or hang at import. Pin `torch` CPU wheel from
  `https://download.pytorch.org/whl/cpu`.
- Pandas reading a 50GB CSV → OOM. Use `chunksize=`, `dtype=`, or switch
  to polars/duckdb.
- `random_state=42` everywhere → reproducible BUT also overfits to that
  one split. Run k-fold for honest numbers.
- HF `Trainer` saves every checkpoint by default → fills disk. Set
  `save_total_limit=2`.
- Mixing CPU and GPU tensors → `RuntimeError: Expected all tensors to
  be on the same device`. Always `.to(device)`.
