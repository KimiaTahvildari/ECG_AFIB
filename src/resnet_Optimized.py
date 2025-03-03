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
import tensorflow as tf
from keras.src.models import Model
from tensorflow.keras.layers import Input, Conv1D, BatchNormalization, Activation, Add, GlobalAveragePooling1D, Dense, Dropout
from keras.src.callbacks import EarlyStopping
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

    # Filter out rows where the signal_quality is lower than 0.5
    df = df[df['signal_quality'] >= 0.5]

    # Normalize the data
    features = ['hrv_sdnn', 'hrv_rmssd', "hrv_mean", 'cv']
    scaler = StandardScaler()
    df[features] = scaler.fit_transform(df[features])

    x = df[features]
    y = df['has_AFIB']

    smote = SMOTE(random_state=42)
    x_res, y_res = smote.fit_resample(x, y)

    x_res = x_res.values.reshape((x_res.shape[0], x_res.shape[1], 1))  # Reshape for Conv1D
    y_res = tf.keras.utils.to_categorical(y_res, num_classes=2)  # One-hot encode the labels

    return train_test_split(x_res, y_res, test_size=0.2, random_state=42)


def residual_block(x, filters, kernel_size=1, stride=1):
    shortcut = x

    x = Conv1D(filters, kernel_size, strides=stride, padding='same')(x)
    x = BatchNormalization()(x)
    x = Activation('relu')(x)

    x = Conv1D(filters, kernel_size, strides=1, padding='same')(x)
    x = BatchNormalization()(x)

    # If the input and output have different dimensions, adjust the shortcut
    if shortcut.shape[-1] != filters:
        shortcut = Conv1D(filters, kernel_size=1, strides=stride, padding='same')(shortcut)
        shortcut = BatchNormalization()(shortcut)

    x = Add()([x, shortcut])
    x = Activation('relu')(x)

    return x


def build_resnet_model(input_shape, num_classes, hp):
    inputs = Input(shape=input_shape)

    x = Conv1D(64, kernel_size=7, strides=2, padding='same')(inputs)
    x = BatchNormalization()(x)
    x = Activation('relu')(x)
    x = tf.keras.layers.MaxPooling1D(pool_size=3, strides=2, padding='same')(x)

    num_blocks = hp.suggest_int('num_blocks', 2, 5)
    filters = hp.suggest_int('filters', 64, 256, step=64)

    for _ in range(num_blocks):
        x = residual_block(x, filters)

    x = residual_block(x, filters * 2, stride=2)
    for _ in range(num_blocks - 1):
        x = residual_block(x, filters * 2)

    x = GlobalAveragePooling1D()(x)
    x = Dense(hp.suggest_int('dense_units', 128, 512, step=128), activation='relu')(x)
    x = Dropout(hp.suggest_float('dropout', 0.2, 0.5))(x)
    outputs = Dense(num_classes, activation='softmax')(x)

    model = Model(inputs, outputs)
    model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
    return model


def objective(trial):
    df = load_data(filename)
    x_train, x_test, y_train, y_test = prepare_data(df)

    input_shape = (x_train.shape[1], x_train.shape[2])
    model = build_resnet_model(input_shape, num_classes=2, hp=trial)

    early_stopping = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
    model.fit(x_train, y_train, epochs=100, batch_size=32, validation_split=0.2, callbacks=[early_stopping], verbose=0)

    y_pred_proba = model.predict(x_test)
    y_pred = np.argmax(y_pred_proba, axis=1)
    y_true = np.argmax(y_test, axis=1)

    accuracy = accuracy_score(y_true, y_pred)
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
    pdf_filename = "../reports/model_evaluation_ResNet_optm.pdf"
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
    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=50)

    best_trial = study.best_trial
    print(f'Best trial: Value: {best_trial.value}')
    for key, value in best_trial.params.items():
        print(f'    {key}: {value}')

    df = load_data(filename)
    x_train, x_test, y_train, y_test = prepare_data(df)
    input_shape = (x_train.shape[1], x_train.shape[2])
    best_model = build_resnet_model(input_shape, num_classes=2, hp=best_trial)
    early_stopping = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
    best_model.fit(x_train, y_train, epochs=100, batch_size=32, validation_split=0.2, callbacks=[early_stopping])

    evaluate_model(best_model, x_test, y_test)


if __name__ == "__main__":
    filename = '../data/afdb_data.csv'
    
    main()
