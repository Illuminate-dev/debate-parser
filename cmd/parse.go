package cmd

import (
	"fmt"
	"path/filepath"

	"debate-parser/internal"

	"github.com/sirupsen/logrus"
	"github.com/spf13/cobra"
)

var (
	batchSize  int
	skipDedupe bool
)

var parseCmd = &cobra.Command{
	Use:   "parse <docx file>",
	Short: "parse a .docx document",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		inputFile := args[0]
		logrus.WithFields(logrus.Fields{
			"file": inputFile,
			"ext":  filepath.Ext(inputFile),
		}).Info("Starting parse operation")

		var storage internal.CardStorage
		if csvFilepath != "" {
			logrus.WithField("path", csvFilepath).Info("Using CSV storage")
			storage = internal.NewCSVStorage(csvFilepath)
		} else if dbURL != "" {
			logrus.WithField("url", dbURL).Info("Using PostgreSQL storage")
			s, err := internal.NewPostgresStorage(dbURL)
			storage = s
			if err != nil {
				logrus.WithError(err).Error("Failed to connect to database")
				return err
			}
		}
		defer storage.Close()

		p := internal.NewParser(storage)
		if batchSize > 0 {
			p.SetBatchSize(batchSize)
			logrus.WithField("batch_size", batchSize).Info("Using custom batch size")
		}

		switch filepath.Ext(inputFile) {
		case ".docx":
			logrus.Info("Parsing single DOCX file")
			err := p.ParseDocx(inputFile)
			if err != nil {
				logrus.WithError(err).Error("Failed to parse DOCX file")
				return err
			}
		case ".zip":
			logrus.Info("Parsing ZIP archive containing DOCX files")
			err := p.ParseZip(inputFile)
			if err != nil {
				logrus.WithError(err).Error("Failed to parse ZIP file")
				return err
			}
		default:
			logrus.WithField("ext", filepath.Ext(inputFile)).Error("Unsupported file type")
			return fmt.Errorf("unsupported file type: %s", filepath.Ext(inputFile))
		}

		logrus.Info("Saving parsed cards")
		err := p.Save()
		if err != nil {
			logrus.WithError(err).Error("Failed to save cards")
			return err
		}

		if skipDedupe {
			logrus.Info("Skipping duplicate removal process")
			logrus.Info("Parse operation completed successfully")
			return nil
		}

		logrus.Info("Starting duplicate removal process")
		err = p.RemoveDuplicates()
		if err != nil {
			logrus.WithError(err).Error("Failed to remove duplicates")
			return err
		}

		logrus.Info("Parse operation completed successfully")
		return nil
	},
}

func init() {
	rootCmd.AddCommand(parseCmd)
	parseCmd.Flags().IntVar(&batchSize, "batch-size", 1000, "Number of cards to accumulate before saving")
	parseCmd.Flags().BoolVar(&skipDedupe, "skip-dedupe", false, "Skip duplicate detection after parsing")
}
