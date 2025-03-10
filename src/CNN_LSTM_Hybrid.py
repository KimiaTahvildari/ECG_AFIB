import joblib
import pandas as pd
import numpy as np
import os
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report, roc_auc_score
from sklearn.preprocessing import StandardScaler
from imblearn.over_sampling import SMOTE
import seaborn as sns
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from keras.src.callbacks import EarlyStopping
from keras.src.utils import to_categorical
from keras import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, Conv1D, MaxPooling1D, Flatten, BatchNormalization
import optuna


def load_data(file_path):
    return pd.read_csv(file_path)


def prepare_data(df):
    # Filter out rows where SDNN > 500 ms
    df = df[df['hrv_sdnn'] <= 500]

    # Filter out rows where RMSSD > 500 ms
    df = df[df['hrv_rmssd'] <= 500]

    # Filter out rows where cv > 0.5 (50 % variability)
    df = df[df['cv'] <= 0.5]

    # Filter out rows where the signal_quality is lower than 0.3
    df = df[df['signal_quality'] >= 0.3]

    # Normalize the data
    features = ['hrv_sdnn', 'hrv_rmssd', "hrv_mean", 'cv', "heart_rate_std", "heart_rate_mean", "sd1", "sd2"]
    scaler = StandardScaler()
    df[features] = scaler.fit_transform(df[features])

    # Prepare the data
    x = df[features]
    y = df['num_AFIB_annotations']  # Target: whether the patient has AFib

    smote = SMOTE(random_state=42)
    x_res, y_res = smote.fit_resample(x, y)

    x_res = x_res.values.reshape((x_res.shape[0], x_res.shape[1], 1))
    y_res = to_categorical(y_res)

    return train_test_split(x_res, y_res, test_size=0.2, random_state=42)


def build_cnn_lstm_model(input_shape, num_filters, kernel_size, lstm_units, dropout_rate):
    model = Sequential()
    model.add(Conv1D(filters=num_filters, kernel_size=kernel_size, activation='relu', input_shape=input_shape))
    model.add(BatchNormalization())
    model.add(MaxPooling1D(pool_size=2))
    model.add(Conv1D(filters=num_filters * 2, kernel_size=kernel_size, activation='relu'))
    model.add(BatchNormalization())
    model.add(MaxPooling1D(pool_size=2))
    model.add(Flatten())

    model.add(LSTM(lstm_units, return_sequences=True))
    model.add(Dropout(dropout_rate))
    model.add(LSTM(lstm_units, return_sequences=True))
    model.add(Dropout(dropout_rate))
    model.add(LSTM(lstm_units))
    model.add(Dropout(dropout_rate))
    model.add(Dense(2, activation='softmax'))
    model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])

    # Save the model
    model.save('../models/CNN_LSTM_Hybrid_model.keras')

    return model


def objective(trial):
    num_filters = trial.suggest_categorical('num_filters', [32, 64, 128])
    kernel_size = trial.suggest_int('kernel_size', 1, 2)
    lstm_units = trial.suggest_int('lstm_units', 50, 150)
    dropout_rate = trial.suggest_float('dropout_rate', 0.2, 0.5)

    model = build_cnn_lstm_model(input_shape, num_filters, kernel_size, lstm_units, dropout_rate)

    early_stopping = EarlyStopping(monitor='val_loss', patience=5)
    model.fit(x_train, y_train, epochs=50, batch_size=32, validation_split=0.2, callbacks=[early_stopping], verbose=0)

    score = model.evaluate(x_test, y_test, verbose=0)
    accuracy = score[1]
    return accuracy


def evaluate_model(model, x_test, y_test):
    y_pred_proba = model.predict(x_test)
    y_pred = np.argmax(y_pred_proba, axis=1)
    y_true = np.argmax(y_test, axis=1)

    accuracy = accuracy_score(y_true, y_pred)
    conf_matrix = confusion_matrix(y_true, y_pred)
    roc_auc = roc_auc_score(y_test, y_pred_proba)
    class_report = classification_report(y_true, y_pred, output_dict=True)

    class_report_df = pd.DataFrame(class_report).transpose()
    class_report_df['labels'] = class_report_df.index
    cols = class_report_df.columns.tolist()
    cols = [cols[-1]] + cols[:-1]
    class_report_df = class_report_df[cols]

    create_classification_report_image(class_report_df)
    create_pdf(accuracy, roc_auc, conf_matrix)
    delete_images()


def create_classification_report_image(class_report_df):
    plt.figure(figsize=(12, 8))
    plt.axis('off')
    cell_text = class_report_df.values
    table = plt.table(cellText=cell_text,
                      colLabels=class_report_df.columns,
                      loc='center',
                      cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2)
    plt.savefig("classification_report.png", bbox_inches='tight')
    plt.close()


def create_pdf(accuracy, roc_auc, conf_matrix):
    pdf_filename = "../reports/model_evaluation_CNN_LSTM.pdf"
    c = canvas.Canvas(pdf_filename, pagesize=letter)
    width, height = letter

    c.drawImage("classification_report.png", 55, 250, width=500, preserveAspectRatio=True, mask='auto')
    c.drawString(270, height - 50, "Accuracy")
    c.drawString(242, height - 70, f"{accuracy}")
    c.drawString(255, height - 100, "ROC AUC Score")
    c.drawString(242, height - 120, f"{roc_auc}")
    c.drawString(245, height - 150, "Classification Report")

    plt.figure(figsize=(8, 6))
    sns.heatmap(conf_matrix, annot=True, fmt='d', cmap='Blues')
    plt.xlabel('Predicted')
    plt.ylabel('Actual')
    plt.title('Confusion Matrix')
    plt.savefig("confusion_matrix.png", bbox_inches='tight')
    plt.close()

    c.drawImage("confusion_matrix.png", 65, 0, width=500, preserveAspectRatio=True, mask='auto')
    c.showPage()
    c.save()


def delete_images():
    os.remove("classification_report.png")
    os.remove("confusion_matrix.png")


def main():
    global x_train, x_test, y_train, y_test, input_shape

    df = load_data(filename)
    x_train, x_test, y_train, y_test = prepare_data(df)

    input_shape = (x_train.shape[1], x_train.shape[2])

    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=50)
    print('Best hyperparameters: ', study.best_params)

    best_model = build_cnn_lstm_model(input_shape, **study.best_params)
    early_stopping = EarlyStopping(monitor='val_loss', patience=5)
    best_model.fit(x_train, y_train, epochs=50, batch_size=32, validation_split=0.2, callbacks=[early_stopping])

    evaluate_model(best_model, x_test, y_test)


if __name__ == "__main__":
    filename = '../data/afdb_data.csv'

    main()
