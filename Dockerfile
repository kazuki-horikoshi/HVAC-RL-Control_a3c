# Specify the base image. Choose an appropriate version if an older version of Python is needed.
FROM python:3.5

# Set the working directory
WORKDIR /usr/src/app

# Copy the requirements.txt file to the container to install Python library dependencies
COPY requirements.txt ./

# Install dependencies listed in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source code to the container
COPY . .

# Set the default command, for example, to launch Jupyter Notebook.
CMD ["jupyter", "notebook", "--ip=0.0.0.0", "--allow-root", "--no-browser"]
