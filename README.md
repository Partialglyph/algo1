FreightScope

Predictive shipping price and  forecasting prototype

Set up local server:

cd C:\Users\owenk\algo1

.\.venv\Scripts\activate.bat

uvicorn shipping_forecast.api:app --host 0.0.0.0 --port 8000


Set up frontend:

cd C:\Users\owenk\algo1\frontend

python -m http.server 5500


Use browser:

http://localhost:5500/index.html

