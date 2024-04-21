# HVAC-RL-Control
"""
add Dockerfile to satisfy the library environment, on python 3.5 #Kazuki Horikoshi 2024,April
"""
We should update if docker won't work properly.
wsl --update

run docker file with below code.
docker build -t my-python-app .

### eplus-env
The EnergyPlus environment is in src/core/eplus-env