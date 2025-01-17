import pandas as pd
import numpy as np
from sklearn.model_selection import GroupKFold
from sklearn.model_selection import cross_val_predict
from sklearn.model_selection import RandomizedSearchCV
from pyprojroot import here
import math
import pickle
import os
import shutil
import time

class MyModel():

    def __init__(self, experiment_name, dataset, regressor, nans_ok=False, month=True, year=True, features=["x", "y", "Elevation", "Slope", "Soil", "Aspect", "TWI", "PET"]):

        # locate the natural and agricultural datasets you may want to use
        self.train_data_loc = str(here("data/3_for_counterfactual/training_data/train")) + "/" + dataset + ".csv"
        self.test_data_loc = str(here("data/3_for_counterfactual/training_data/test")) + "/" + dataset + ".csv"

        self.regressor = regressor
        self.nans_ok = nans_ok
        self.features = features
        self.experiment_name = experiment_name
        self.experiment_path = str(here("data/4_for_analysis/ML_outputs/experiments")) + "/" + experiment_name
        self.month = month
        self.year = year

        if not os.path.exists(self.experiment_path):
            os.makedirs(self.experiment_path)

        # save the scripts that generated and called this object to the experiments folder
        shutil.copy(here("code/2_counterfactual/experiments.py"), self.experiment_path + "/experiments.py")
        shutil.copy(here("code/2_counterfactual/model_class.py"), self.experiment_path + "/model_class.py")

    def prepare_dataset(self, df):

        if self.nans_ok == False:
            df = df.fillna(-9999)

        # split between predictors and predicted
        X = df[self.features]
        y = df['ET']

        if self.month:
            df = pd.get_dummies(df, columns=["month"])
            X_months = df[df.columns[df.columns.str.startswith("month")]]
            X = pd.concat([X, X_months], join = 'outer', axis = 1)

        if self.year:
            df = pd.get_dummies(df, columns=["year"])
            X_years = df[df.columns[df.columns.str.startswith("year")]]
            X = pd.concat([X, X_years], join = 'outer', axis = 1)

        return X, y, df

    def tune_hyperparameters(self, train_or_test="train"):
        # This function gets hyperparameters using the training data and sets them in the regressor

        # retrieve the dataset 
        if train_or_test=="train":
            df = pd.read_csv(self.train_data_loc)
        else:
            df = pd.read_csv(self.test_data_loc)
        # there are about 200 pixels in each 1 km grid. try taking 0.005 of the data
        df = df.sample(frac = 0.005)

        X, y, df = self.prepare_dataset(df)

        # try to improve the RF by tuning hyperparameters
        # see: https://towardsdatascience.com/hyperparameter-tuning-the-random-forest-in-python-using-scikit-learn-28d2aa77dd74

        # Number of trees in random forest
        n_estimators = [int(x) for x in np.linspace(start = 100, stop = 2000, num = 100)]
        # Maximum number of levels in tree
        max_depth = [int(x) for x in np.linspace(10, 110, num = 11)]
        max_depth.append(None)
        # Minimum number of samples required to split a node
        min_samples_split = [200]
        # Minimum number of samples required at each leaf node
        min_samples_leaf = [100]
        # The number of features to consider while searching for a best split. 
        max_features = [2, 3]

        # Create the random grid
        random_grid = {'n_estimators': n_estimators,
                    'max_depth': max_depth,
                    'min_samples_split': min_samples_split,
                    'min_samples_leaf': min_samples_leaf, 
                    'max_features': max_features}

        # Use the random grid to search for best hyperparameters
        # Random search of parameters, using 3 fold cross validation, 
        # search across 100 different combinations, and use all available cores
        random_search = RandomizedSearchCV(estimator = self.regressor, param_distributions = random_grid, n_iter = 100, cv = 3, verbose=2, random_state=42, n_jobs = -1)
        random_search.fit(X, y)
        hyperparameters = random_search.best_params_

        self.regressor.set_params(**hyperparameters) # use the parameters from the randomized search

        return hyperparameters

    def crossval(self, train_or_test="train", distances=[50000, 20000, 10000, 5000, 2000, 1]):

        # retrieve the dataset to crossvalidate over
        if train_or_test=="train":
            df = pd.read_csv(self.train_data_loc)
        elif train_or_test=="test":
            df = pd.read_csv(self.test_data_loc)
        else: 
            Exception("train_or_test must be 'train' or 'test'")

        X, y, df = self.prepare_dataset(df)
        
        cols = list(df) + ['fold_size', 'cv_fold', "ET_pred"]
        cv_df = pd.DataFrame(columns=cols)

        for dist in distances: 

            df = df.assign(fold_size=dist)

            # I first generate an extra column for my dataset called cv_fold which corresponds to its location

            # 1. Convert to miles to degrees. See: https://www.nhc.noaa.gov/gccalc.shtml
            # 2. Divide by number of degrees
            # 3. Floor operation
            # 4. turn back into coordinates
            # 5. String together

            x_size = dist/89000 # 1 degree lon (x) = 89km = 89000m
            y_size = dist/111000 # 1 degree lat (y) = 111km = 111000m
            
            # add a column to the df that indicates which crossvalidation group it falls into
            df = df.assign(cv_fold = lambda x: x.x.apply(lambda val: str(math.floor(val/x_size)*x_size)) +","+ x.y.apply(lambda val: str(math.floor(val/y_size)*y_size)))
            print(df.head(), flush=True)

            # How many folds = number of cells or cv_folds
            # n_fold = df.cv_fold.nunique() # set is same as unique function in R
            # print(n_fold, flush=True)
            kf = GroupKFold(5) #leave out 20% of the data at a time
            split = kf.split(df, groups = df['cv_fold'])

            print("predictions beginning", flush=True)
            start = time.time()
            y_pred = cross_val_predict(self.regressor, X, y, cv=split, verbose=1, n_jobs = -1)
            end = time.time()
            print("predictions completed; time elapsed: "+str(end-start), flush=True)

            df = df.assign(ET_pred=y_pred)
            cv_df = pd.concat([cv_df, df], axis = 0)

        
        # save the full predictions using the spatial CV
        cv_df.to_csv(self.experiment_path+"/crossval_predictions_" + train_or_test + ".csv", index=False)
        print("crossval predictions saved", flush=True)

        return

    def train_model(self, train_or_test="train"):

        print("Training model from scratch; loading dataset", flush=True)  

        # load full dataset
        if train_or_test=="train":
            df = pd.read_csv(self.train_data_loc)
        else:
            df = pd.read_csv(self.test_data_loc)

        X, y, df = self.prepare_dataset(df)

        print("regressor defined, training beginning", flush=True)
        self.regressor.fit(X, y)
        print("training completed; pickle beginning", flush=True)

        # pickle the trained model
        with open(self.experiment_path+"/trained_model_"+train_or_test+".pkl", 'wb') as f:
            pickle.dump(self.regressor, f)
        print("pickle completed", flush=True)

        return

    def predictions(self, ag_or_fallow="agriculture"):
        
        # are you predicting over all agriculture or only fallow lands? 
        application_data_location = str(here("data/3_for_counterfactual/agriculture")) + "/" + ag_or_fallow + ".csv"
        df = pd.read_csv(application_data_location)

        X, y, df = self.prepare_dataset(df)

        y_pred = self.regressor.predict(X)
        df = df.assign(ET_pred=y_pred)

        # calculate the difference between the actual and counterfactual ET
        df['ag_ET'] = df.ET- df.ET_pred
        print("prediction completed; saving beginning", flush=True)

        # save the new dataset
        df.to_csv(self.experiment_path+"/"+ag_or_fallow+".csv", index=False)

        return

# test code for debugging 
if __name__ == '__main__':

    from sklearn.ensemble import RandomForestRegressor
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.ensemble import GradientBoostingRegressor

    # first, define your model 
    model = MyModel(experiment_name="trial_model", 
                    dataset="fallow", 
                    regressor=RandomForestRegressor(n_estimators=100, verbose=1, random_state=0, n_jobs = -1), 
                    nans_ok=False, # whether it's ok to have nans in the data
                    month=True, # whether the data has a month variable
                    year=True, # whether the data has a year variable
                    features=["x", "y", "Elevation", "Slope", "Soil", "Aspect", "TWI", "PET"])

    # second, tune hyperparameters 
    model.tune_hyperparameters(train_or_test="train")

    # optionally, perform a cross-validation using the training set -- only if there's large spatial gaps in available data
    # model.crossval(train_or_test="train")

    # third, generate new predictions for fallow lands
    model.train_model(train_or_test="train")
    model.predictions(ag_or_fallow="fallow_val")
    model.predictions(ag_or_fallow="fallow_test")
    model.predictions(ag_or_fallow="agriculture_dwr_years")