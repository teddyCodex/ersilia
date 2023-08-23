import os
import json
import click
import tempfile
import types
import subprocess
import time 
import re
from ..cli import echo
from ..io.input import ExampleGenerator
from .. import ErsiliaBase
from .. import throw_ersilia_exception
from .. import ErsiliaModel
from ..utils.exceptions_utils import test_exceptions as texc
from ..core.session import Session
from ..default import INFORMATION_FILE

try:
    from fuzzywuzzy import fuzz
except:
    fuzz = None

RUN_FILE = "run.sh"
DATA_FILE = "data.csv"
DIFFERENCE_THRESHOLD = 5    # outputs should be within this percent threshold to be considered consistent
NUM_SAMPLES = 5
BOLD = '\033[1m'
RESET = '\033[0m'


class ModelTester(ErsiliaBase):
    def __init__(self, model_id, config_json=None):
        ErsiliaBase.__init__(self, config_json=config_json, credentials_json=None)
        self.model_id = model_id
        self.model_size = 0 
        self.tmp_folder = tempfile.mkdtemp(prefix="ersilia-")
        self._info = self._read_information()
        self._input = self._info["card"]["Input"]
        self.RUN_FILE = "run.sh"
        self.information_check = False
        self.single_input = False
        self.example_input = False
        self.consistent_output = False
        self.run_using_bash = False

    def _read_information(self):
        json_file = os.path.join(self._dest_dir, self.model_id, INFORMATION_FILE)
        self.logger.debug("Reading model information from {0}".format(json_file))
        if not os.path.exists(json_file): 
            raise texc.InformationFileNotExist(self.model_id)
        with open(json_file, "r") as f:
            data = json.load(f)
        return data
    
    """
    This function uses the fuzzy wuzzy package to compare the differences between outputs when 
    they're strings and not floats. The fuzz.ratio gives the percent of similarity between the two outputs.
    Example: two strings that are the exact same will return 100
    """
    def _compare_output_strings(self, output1, output2):
        if output1 is None and output2 is None: 
            return 100
        else:
            return fuzz.ratio(output1, output2)

    """
    To compare outputs, we are stating that numbers generated by the models need to be within 5% of each 
    other in order to be considered consistent. This function returns true if the outputs are within that 
    5% threshold (meaning they're consistent), and false if they are not (meaning they are not consistent).
    """
    def _is_below_difference_threshold(self, output1, output2): 
        if output1 == 0.0 or output2 == 0.0: 
            return output1 == output2
        elif output1 is None or output2 is None: 
            return output1 == output2
        else:
            return (100 * (abs(output1 - output2) / ((output1 + output2) / 2)) < DIFFERENCE_THRESHOLD)
             

    """
    When the user specifies an output file, the file will show the user how big the model is. This function 
    calculates the size of the model to allow this. 
    """
    def _set_model_size(self, directory):
        for dirpath, dirnames, filenames in os.walk(directory):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                self.model_size += os.path.getsize(file_path)

    """
    This helper method was taken from the run.py file, and just prints the output for the user 
    """
    def _print_output(self, result, output): 
        echo("Printing output...")

        if isinstance(result, types.GeneratorType): 
            for r in result: 
                if r is not None: 
                    if output is not None:
                        with open(output.name, 'w') as file:
                            json.dump(r, output.name)
                    else: 
                        echo(json.dumps(r, indent=4))
                else: 
                    if output is not None:
                        message = echo("Something went wrong", fg="red")
                        with open(output.name, 'w') as file:
                            json.dump(message, output.name)
                    else: 
                        echo("Something went wrong", fg="red")
                        
        else:
            echo(result)
        

    """
    This helper method checks that the model ID is correct.
    """
    def _check_model_id(self, data): 
        print("Checking model ID...")
        if data["card"]["Identifier"] != self.model_id:
            raise texc.WrongCardIdentifierError(self.model_id)    


    """
    This helper method checks that the slug field is non-empty.
    """
    def _check_model_slug(self, data): 
        print("Checking model slug...")
        if not data["card"]["Slug"]:
            raise texc.EmptyField("slug")
   
    """
    This helper method checks that the description field is non-empty.
    """
    def _check_model_description(self, data): 
        print("Checking model description...")
        if not data["card"]["Description"]:
            raise texc.EmptyField("Description")

    """
    This helper method checks that the model task is one of the following valid entries:
        - Classification
        - Regression
        - Generative
        - Representation
        - Similarity
        - Clustering
        - Dimensionality reduction
    """
    def _check_model_task(self, data): 
        print("Checking model task...")
        valid_tasks = [ 'Classification', 'Regression', 'Generative' , 'Representation', 
                        'Similarity',  'Clustering',  'Dimensionality reduction']
        sep = ', '
        tasks = []
        if sep in data["card"]["Task"]:
            tasks = data["card"]["Task"].split(sep)
        else:
            tasks = data["card"]["Task"]
        for task in tasks:
            if task not in valid_tasks:
                raise texc.InvalidEntry("Task")
    
    """
    This helper method checks that the input field is one of the following valid entries:
        - Compound
        - Protein
        - Text
    """
    def _check_model_input(self, data): 
        print("Checking model input...")
        valid_inputs = [[ 'Compound' ], [ 'Protein' ], [ 'Text' ]]
        if data["card"]["Input"] not in valid_inputs:
            raise texc.InvalidEntry("Input")

    """
    This helper method checks that the input shape field is one of the following valid entries:
        - Single
        - Pair
        - List
        - Pair of Lists
        - List of Lists
    """
    def _check_model_input_shape(self, data): 
        print("Checking model input shape...")
        valid_input_shapes = ["Single", "Pair", "List", "Pair of Lists", "List of Lists"]
        if data["card"]["Input Shape"] not in valid_input_shapes:
            raise texc.InvalidEntry("Input Shape")

    """
    This helper method checks the the output is one of the following valid entries:
        - Boolean
        - Compound
        - Descriptor
        - Distance
        - Experimental value
        - Image
        - Other value
        - Probability
        - Protein
        - Score
        - Text
    """
    def _check_model_output(self, data): 
        print("Checking model output...")
        valid_outputs = [ 'Boolean', 'Compound', 'Descriptor', 'Distance', 'Experimental value', 
                          'Image', 'Other value', 'Probability', 'Protein', 'Score', 'Text']
        sep = ', '
        outputs = []
        if sep in data["card"]["Output"]:
            outputs = data["card"]["Output"].split(sep)
        else:
            outputs = data["card"]["Output"]
        for output in outputs:
            if output not in valid_outputs:
                raise texc.InvalidEntry("Output")

    """
    This helper method checks that the output type is one of the following valid entries:
        - String
        - Float
        - Integer
    """
    def _check_model_output_type(self, data): 
        print("Checking model output type...")
        valid_output_types = [[ 'String' ], [ 'Float' ], [ 'Integer' ]]
        if data["card"]["Output Type"] not in valid_output_types:
            raise texc.InvalidEntry("Output Type")

    """
    This helper method checks that the output shape is one of the following valid entries:
        - Single
        - List
        - Flexible List
        - Matrix
        - Serializable Object
    """
    def _check_model_output_shape(self, data): 
        print("Checking model output shape...")
        valid_output_shapes = ["Single", "List", "Flexible List", "Matrix", "Serializable Object"]
        if data["card"]["Output Shape"] not in valid_output_shapes:
            raise texc.InvalidEntry("Output Shape")

    """
    This is a helper function for the run_bash() function, and it parses through the Dockerfile to find 
    the package installation lines.
    """
    def _parse_dockerfile(self, temp_dir, pyversion):
        packages = set()
        prefix = "FROM bentoml/model-server:0.11.0-py"
        os.chdir(temp_dir) # navigate into cloned repo
        with open('Dockerfile', 'r') as dockerfile:
            lines = dockerfile.readlines()
            assert lines[0].startswith(prefix)
            pyversion[0] = lines[0][len(prefix):]
            lines_as_string = '\n'.join(lines)
            run_lines = re.findall(r'^\s*RUN\s+(.+)$', lines_as_string, re.MULTILINE)
        return run_lines


    """
    Check the model information to make sure it's correct. Performs the following checks:
    - Checks that model ID is correct
    - Checks that model slug is non-empty
    - Checks that model description is non-empty
    - Checks that the model task is valid
    - Checks that the model input, input shape is valid
    - Checks that the model output, output type, output shape is valid
    """
    @throw_ersilia_exception
    def check_information(self, output):
        self.logger.debug("Checking that model information is correct")
        print(BOLD + "Beginning checks for {0} model information:".format(self.model_id) + RESET)
        json_file = os.path.join(self._dest_dir, self.model_id, INFORMATION_FILE)
        with open(json_file, "r") as f:
            data = json.load(f)

        self._check_model_id(data)
        self._check_model_slug(data)
        self._check_model_description(data)
        self._check_model_task(data)
        self._check_model_input(data)
        self._check_model_input_shape(data)
        self._check_model_output(data)
        self._check_model_output_type(data)
        self._check_model_output_shape(data)
        print("SUCCESS! Model information verified.\n")

        if output is not None:
            self.information_check = True


    """
    Runs the model on a single smiles string and prints to the user if no output is specified.
    """
    @throw_ersilia_exception
    def check_single_input(self, output):
        session = Session(config_json=None)
        service_class = session.current_service_class()
        input = "COc1ccc2c(NC(=O)Nc3cccc(C(F)(F)F)n3)ccnc2c1"

        click.echo(BOLD + "Testing model on single smiles input...\n" + RESET)
        mdl = ErsiliaModel(self.model_id, service_class=service_class, config_json=None)
        result = mdl.run(input=input, output=output, batch_size=100)

        if output is not None:
            self.single_input = True
        else: 
            self._print_output(result, output)

    """
    Generates an example input of 5 smiles using the 'example' command, and then tests the model on that input and prints it
    to the consol if no output file is specified by the user.
    """
    @throw_ersilia_exception
    def check_example_input(self, output):
        session = Session(config_json=None)
        service_class = session.current_service_class()
        eg = ExampleGenerator(model_id=self.model_id)
        input = eg.example(n_samples=NUM_SAMPLES, file_name=None, simple=True)

        click.echo(BOLD + "\nTesting model on input of 5 smiles given by 'example' command...\n" + RESET)
        mdl = ErsiliaModel(self.model_id, service_class=service_class, config_json=None)
        result = mdl.run(input=input, output=output, batch_size=100) 

        if output is not None:
            self.example_input = True
        else: 
            self._print_output(result, output)


    """
    Gets an example input of 5 smiles using the 'example' command, and then runs this same input on the 
    model twice. Then, it checks if the outputs are consistent or not and specifies that to the user. If 
    it is not consistent, an InconsistentOutput error is raised. Lastly, it makes sure that the number of 
    outputs equals the number of inputs.  
    """
    @throw_ersilia_exception
    def check_consistent_output(self):
        # self.logger.debug("Confirming model produces consistent output...")
        click.echo(BOLD + "\nConfirming model produces consistent output..." + RESET)

        session = Session(config_json=None)
        service_class = session.current_service_class()

        eg = ExampleGenerator(model_id=self.model_id)
        input = eg.example(n_samples=NUM_SAMPLES, file_name=None, simple=True)

        mdl1 = ErsiliaModel(self.model_id, service_class=service_class, config_json=None)
        mdl2 = ErsiliaModel(self.model_id, service_class=service_class, config_json=None)
        result = mdl1.run(input=input, output=None, batch_size=100)
        result2 = mdl2.run(input=input, output=None, batch_size=100)

        zipped = list(zip(result, result2))

        for item1, item2 in zipped:
            output1 = item1["output"]
            output2 = item2["output"]
            
            keys1 = list(output1.keys())
            keys2 = list(output2.keys())
            
            for key1, key2 in zip(keys1, keys2): 
                
                # check if the output types are not the same 
                if not isinstance(output1[key1], type(output2[key2])):
                    for item1, item2 in zipped:
                        print(item1)
                        print(item2)
                        print('\n')
                    raise texc.InconsistentOutputTypes(self.model_id)
                
                if output1[key1] is None: 
                    continue

                elif isinstance(output1[key1], (float, int)): 

                    # check to see if the first and second outputs are within 5% from each other 
                    if not self._is_below_difference_threshold(output1[key1], output2[key2]):
                        for item1, item2 in zipped:
                            print(item1)
                            print(item2)
                            print('\n')
                        # maybe change it to print all of the outputs, and in the error raised it highlights exactly the ones that were off 
                        raise texc.InconsistentOutputs(self.model_id)
                elif isinstance(output1[key1], list): 
                    ls1 = output1[key1]
                    ls2 = output2[key2]

                    for elem1, elem2 in zip(ls1, ls2):
                        if isinstance(elem1, float): # if one of the outputs is a float, then that means the other is a float too
                            if not self._is_below_difference_threshold(elem1, elem2):
                                for item1, item2 in zipped:
                                    print(item1)
                                    print(item2)
                                    print('\n')
                                raise texc.InconsistentOutputs(self.model_id)
                        else: 
                            if self._compare_output_strings(elem1, elem2) <= 0.95: 
                                print('output1 value:', elem1)
                                print('output2 value:', elem2)
                                raise texc.InconsistentOutputs(self.model_id)
                else: 
                    # if it reaches this, then the outputs are just strings
                    if self._compare_output_strings(output1[key1], output2[key2]) <= 0.95: 
                        print('output1 value:', output1[key1])
                        print('output2 value:', output2[key2])
                        raise texc.InconsistentOutputs(self.model_id)
                
        self.consistent_output = True
        print("Model output is consistent!")
    
        click.echo(BOLD + "\nConfirming there are same number of outputs as inputs..." + RESET)
        print("Number of inputs:", NUM_SAMPLES)
        print("Number of outputs:", len(zipped))

        if NUM_SAMPLES != len(zipped): 
            raise texc.MissingOutputs()
        else: 
            echo("Number of outputs and inputs are equal!\n")


    # WITH CONDA!!!!
    @throw_ersilia_exception
    def run_bash(self): 
        # print("Running the model bash script...")
        print("Cloning a temporary file and calculating model size...")
        
        # Save current working directory - atm, this must be run from root directory (~)
        # TODO: is there a way to change this so that this test command doesn't have to be run from root dir
        current_dir = os.getcwd()

        # Create temp directory and clone model 
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_url = 'https://github.com/ersilia-os/{0}.git'.format(self.model_id) 
            try:
                subprocess.run(['git', 'clone', repo_url, temp_dir], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error while cloning the repository: {e}")

            # we will remove this part later, but will keep until we get the run_bash() function working
            self._set_model_size(temp_dir)
            size_kb = self.model_size / 1024
            size_mb = size_kb / 1024
            size_gb = size_mb / 1024
            print("\nModel Size:")
            print("KB:", size_kb)
            print("MB:", size_mb)
            print("GB:", size_gb)
            return
        
            # halt this check if the run.sh file does not exist (e.g. eos3b5e)
            if not os.path.exists(os.path.join(temp_dir, "model/framework/run.sh")):
                print("Check halted: run.sh file does not exist.")
                return

            # Navigate into the temporary directory
            subdirectory_path = os.path.join(temp_dir, "model/framework")
            os.chdir(subdirectory_path)

            # Parse Dockerfile 
            #dockerfile_path = os.path.join(temp_dir, "Dockerfile")
            pyversion = [0]
            packages = self._parse_dockerfile(temp_dir, pyversion)
            pyversion[0] = pyversion[0][0] + '.' + pyversion[0][1:]

            conda_env_name = self.model_id
            try:
                # subprocess.run(['conda', 'create', '-n', self.model_id, 'python={0}'.format(pyversion[0])], check=True)
                subprocess.run(['conda', 'create', '-n', self.model_id, 'python={0}'.format('3.10.0')], check=True)
                subprocess.run(['conda', 'activate', conda_env_name], shell=True, check=True)

                # install packages
                for package in packages:
                    if 'conda install' in package:
                        # Handle conda package installation
                        subprocess.run(package, shell=True, check=True)
                    elif 'pip install' in package:
                        subprocess.run(package, shell=True, check=True)
                    else:
                        print("Invalid package command:", package)
                print("Packages printed!")
                
                # Create temp file
                with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
                    temp_file_path = temp_file.name

                # Run bash script with specified args
                output_path = temp_file_path
                run_path = os.path.join(temp_dir, "model/framework/run.sh")      # path to run.sh
                arg1 = os.path.join(current_dir, "ersilia/test/inputs/compound_singles.csv")      # input
                arg2 = output_path      # output

                try:
                    subprocess.run(['bash', run_path, ".", arg1, arg2,], check=True)
                except subprocess.CalledProcessError as e:
                    print(f"Error while running the bash script: {e}")

                with open(output_path, 'r') as temp_file:
                    output_contents = temp_file.read()

                print("Output contents:")
                print(output_contents)

                deactivate_command = "conda deactivate"
                subprocess.run(deactivate_command, shell=True, check=True)

            except Exception as e:
                    print(f"Error while creating or activating the conda environment: {e}")

    
    """
    writes to the .json file all the basic information received from the test module:
    - size of the model
    - did the basic checks pass? True or False
    - time to run the model
    - did the single input run without error? True or False
    - did the run bash run without error? True or False
    - did the example input run without error? True or False 
    - are the outputs consistent? True or False 
    """ 
    def make_output(self, output, time):
        size_kb = self.model_size / 1024
        size_mb = size_kb / 1024
        size_gb = size_mb / 1024

        data = {"model size": {"KB": size_kb, "MB": size_mb, "GB": size_gb}, 
                "time to run tests (seconds)": time, 
                "basic checks passed": self.information_check, 
                "single input run without error": self.single_input, 
                "example input run without error": self.example_input,
                "outputs consistent": self.consistent_output,
                "bash run without error": self.run_using_bash
                }
        with open(output, "w") as json_file:
            json.dump(data, json_file, indent=4)


    def run(self, output_file):
        start = time.time()
        self.check_information(output_file)
        self.check_single_input(output_file)
        self.check_example_input(output_file)
        self.check_consistent_output()
        self.run_bash()
        
        end = time.time()
        seconds_taken = end - start

        if output_file is not None: 
            self.make_output(output_file, seconds_taken)
