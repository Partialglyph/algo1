import pandas as pd

df = pd.read_excel("data.xlsx", sheet_name=0, header=None)
for i in range(len(df)):
    raw = df.iloc[i, 0]
    if "FRED" in str(raw) or "Global" in str(raw):
        print(i, repr(raw))